import { useEffect, useSyncExternalStore } from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";

/**
 * Live-connection status for the queue stream indicator.
 *   connecting   → opening the SSE for the first time (fresh, never dropped)
 *   live         → SSE connected, updates push in real time
 *   reconnecting → SSE dropped AFTER being live, EventSource is retrying
 *   polling      → SSE unavailable/closed, falling back to the 15s poll only
 */
export type StreamStatus = "connecting" | "live" | "reconnecting" | "polling";

/**
 * The SSE connection is a single app-session SINGLETON, not per-component.
 *
 * Why: the indicator lives in EmailWorkspace, which unmounts/remounts as the
 * chair navigates /queue ↔ /tickets/[id]. A per-mount EventSource would reopen
 * on every navigation and flash "reconnecting" (its pre-open state) until the
 * fresh stream's onopen fired — even though nothing was ever disconnected. A
 * module-level connection persists across those remounts, so once it's live it
 * stays live; "reconnecting" now means only a genuine mid-session drop.
 *
 * The connection is opened lazily on first mount and intentionally never closed
 * (the browser tears it down on page unload) — closing on unmount is exactly the
 * reopen-on-navigation behavior we're avoiding.
 */
let source: EventSource | null = null;
let status: StreamStatus = "connecting";
let client: QueryClient | null = null;
const listeners = new Set<() => void>();

function setStatus(next: StreamStatus): void {
  if (next === status) return;
  status = next;
  listeners.forEach((l) => l());
}

function ensureConnected(queryClient: QueryClient): void {
  // Latest client wins for invalidation; stable across a session in practice.
  client = queryClient;
  if (source !== null || typeof window === "undefined") return;
  if (typeof EventSource === "undefined") {
    setStatus("polling");
    return;
  }

  const base = process.env.NEXT_PUBLIC_API_URL ?? "";
  const es = new EventSource(`${base}/emails/stream`);
  source = es;

  es.onopen = () => setStatus("live");

  es.onmessage = () => {
    // Any lifecycle event → refresh the queue and analytics views, plus the
    // currently-open ticket detail (["emailByTicket", id]) so a background change
    // like a completed redraft shows up immediately instead of on its 15s poll.
    client?.invalidateQueries({ queryKey: ["emailQueue"] });
    client?.invalidateQueries({ queryKey: ["analytics"] });
    client?.invalidateQueries({ queryKey: ["emailByTicket"] });
  };

  es.onerror = () => {
    // EventSource auto-reconnects unless CLOSED. Either way the poll fallback
    // keeps data fresh; the indicator just reflects the SSE health.
    setStatus(es.readyState === EventSource.CLOSED ? "polling" : "reconnecting");
  };
}

function subscribe(onChange: () => void): () => void {
  listeners.add(onChange);
  return () => {
    listeners.delete(onChange);
  };
}

function getSnapshot(): StreamStatus {
  return status;
}

function getServerSnapshot(): StreamStatus {
  return "connecting";
}

/**
 * Subscribe to the shared queue-stream status. Opens the singleton EventSource
 * on first use and invalidates the emailQueue + analytics React Query caches on
 * every lifecycle event, so the UI updates the instant an email is created /
 * routed / approved / rerouted — without waiting for the 15s poll (which stays
 * on as a graceful fallback).
 */
export function useEmailQueueStream(): { status: StreamStatus } {
  const queryClient = useQueryClient();

  useEffect(() => {
    ensureConnected(queryClient);
  }, [queryClient]);

  const current = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  return { status: current };
}
