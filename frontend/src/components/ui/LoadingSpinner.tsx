import { cn } from "@/lib/utils";

interface LoadingSpinnerProps {
  size?: "sm" | "md" | "lg";
  className?: string;
}

const SIZE: Record<NonNullable<LoadingSpinnerProps["size"]>, string> = {
  sm: "h-4 w-4 border-2",
  md: "h-6 w-6 border-2",
  lg: "h-9 w-9 border-[3px]",
};

/** A simple CSS-animated ring spinner. */
export function LoadingSpinner({ size = "md", className }: LoadingSpinnerProps) {
  return (
    <span
      className={cn(
        "inline-block animate-spin rounded-full border-solid border-current border-r-transparent align-[-0.125em]",
        SIZE[size],
        className
      )}
      style={{ color: "var(--accent)" }}
      role="status"
      aria-label="Loading"
    />
  );
}
