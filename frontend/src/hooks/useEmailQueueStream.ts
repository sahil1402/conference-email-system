import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

/**
 * Live-connection status for the queue stream indicator.
 *   live         → SSE connected, updates push in real time
 *   reconnecting → SSE dropped, EventSource is retrying (poll still covers us)
 *   polling      → SSE unavailable/closed, falling back to the 15s poll only
 */
export type StreamStatus = "live" | "reconnecting" | "polling";

/**
 * Opens an EventSource to /emails/stream and invalidates the emailQueue +
 * analytics React Query caches on every lifecycle event, so the UI updates the
 * instant an email is created / routed / approved / rerouted — without waiting
 * for the 15s poll (which stays on as a graceful fallback).
 */
export function useEmailQueueStream(): { status: StreamStatus } {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<StreamStatus>("reconnecting");
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (typeof window === "undefined" || typeof EventSource === "undefined") {
      setStatus("polling");
      return;
    }

    const base = process.env.NEXT_PUBLIC_API_URL ?? "";
    const source = new EventSource(`${base}/emails/stream`);
    sourceRef.current = source;

    source.onopen = () => setStatus("live");

    source.onmessage = () => {
      // Any lifecycle event → refresh the queue and analytics views.
      queryClient.invalidateQueries({ queryKey: ["emailQueue"] });
      queryClient.invalidateQueries({ queryKey: ["analytics"] });
    };

    source.onerror = () => {
      // EventSource auto-reconnects unless CLOSED. Either way the poll fallback
      // keeps data fresh; the indicator just reflects the SSE health.
      setStatus(source.readyState === EventSource.CLOSED ? "polling" : "reconnecting");
    };

    return () => {
      source.close();
      sourceRef.current = null;
    };
  }, [queryClient]);

  return { status };
}
