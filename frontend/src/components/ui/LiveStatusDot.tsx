import type { StreamStatus } from "@/hooks/useEmailQueueStream";

const CONFIG: Record<
  StreamStatus,
  { color: string; label: string; pulse: boolean }
> = {
  live: { color: "#10b981", label: "Live", pulse: true },
  reconnecting: { color: "#f59e0b", label: "Reconnecting", pulse: true },
  polling: { color: "#8b91a8", label: "Polling", pulse: false },
};

/**
 * Small connection-status indicator for the live queue stream:
 * green = live SSE, amber = reconnecting, gray = polling fallback.
 */
export function LiveStatusDot({ status }: { status: StreamStatus }) {
  const { color, label, pulse } = CONFIG[status];
  return (
    <span
      className="inline-flex items-center gap-1.5 text-xs"
      style={{ color: "var(--text-muted)" }}
      title={`Queue updates: ${label.toLowerCase()}`}
    >
      <span className="relative inline-flex h-2 w-2">
        {pulse && (
          <span
            className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-60"
            style={{ backgroundColor: color }}
          />
        )}
        <span
          className="relative inline-flex h-2 w-2 rounded-full"
          style={{ backgroundColor: color }}
        />
      </span>
      {label}
    </span>
  );
}
