import { useQuery } from "@tanstack/react-query";

import { getQueueFacets, type QueueFacetsParams } from "@/lib/api";

/** Subscribe to the queue facet counts (status bar + source toggle).
 *
 * Backed by the dedicated GET /emails/queue/facets aggregate, so counts include
 * out-of-window rows (never a capped-page tally). Pass the queue's CONTEXT
 * filters (lane / chair / unassigned / status / search) — NOT the facet
 * dimensions themselves — so the bar/toggle counts compose with the active
 * filters yet stay stable while a status/source is selected. Polls on the same
 * 15s cadence as the queue so the two stay in step. */
export function useQueueFacets(params?: QueueFacetsParams) {
  const { data, isLoading, isError } = useQuery({
    queryKey: params ? ["queueFacets", params] : ["queueFacets"],
    queryFn: () => getQueueFacets(params),
    refetchInterval: 15_000,
  });

  return {
    byZendeskStatus: data?.by_zendesk_status ?? {},
    bySource: data?.by_source ?? {},
    sources: data?.sources ?? [],
    isLoading,
    isError,
  };
}
