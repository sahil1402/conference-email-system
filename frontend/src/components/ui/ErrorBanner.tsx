import { AlertCircle, RotateCw } from "lucide-react";

import { cn } from "@/lib/utils";

interface ErrorBannerProps {
  message: string;
  onRetry?: () => void;
  className?: string;
}

/** A red-tinted error banner with an icon and optional retry button. */
export function ErrorBanner({ message, onRetry, className }: ErrorBannerProps) {
  return (
    <div
      className={cn(
        "flex items-center gap-3 rounded-xl border px-4 py-3 text-sm",
        className
      )}
      style={{
        backgroundColor: "var(--danger-subtle)",
        borderColor: "var(--danger)",
        color: "var(--text-primary)",
      }}
      role="alert"
    >
      <AlertCircle
        className="h-5 w-5 shrink-0"
        style={{ color: "var(--danger)" }}
      />
      <span className="flex-1">{message}</span>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors hover:opacity-80"
          style={{
            color: "var(--danger)",
            backgroundColor: "rgba(239, 68, 68, 0.12)",
          }}
        >
          <RotateCw className="h-3.5 w-3.5" />
          Retry
        </button>
      )}
    </div>
  );
}
