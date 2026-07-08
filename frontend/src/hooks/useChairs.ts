import { useQuery } from "@tanstack/react-query";

import { getChairs, getReassignmentEvents } from "@/lib/api";
import type { Chair } from "@/types";

/**
 * Fetch the chair roster (Phase 6A). The roster changes rarely, so this uses a
 * long stale time rather than polling. Returns a convenience `byId` map for
 * resolving an email's `assigned_chair_id` to its chair.
 */
export function useChairs() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["chairs"],
    queryFn: () => getChairs(),
    staleTime: 5 * 60_000,
  });

  const chairs: Chair[] = data ?? [];
  const byId = new Map<number, Chair>(chairs.map((c) => [c.id, c]));

  return { chairs, byId, isLoading, isError };
}

/**
 * Fetch chair-reassignment events (for the analytics reassignment chart).
 * Derived from the audit feed; refreshed on a short interval so the chart
 * reflects recent corrections.
 */
export function useReassignmentEvents() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["reassignmentEvents"],
    queryFn: getReassignmentEvents,
    staleTime: 60_000,
  });

  return { events: data ?? [], isLoading, isError };
}
