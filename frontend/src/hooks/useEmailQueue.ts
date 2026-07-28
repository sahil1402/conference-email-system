import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { getEmailQueue, type EmailQueueParams } from "@/lib/api";

/** Subscribe to the email review queue (polls every 15s).
 *
 * Pass `params` (e.g. `{ lane: "faq" }`) to fetch a lane-scoped, paginated
 * slice; `total` then reflects that lane's true count, so callers can show an
 * accurate stat without counting the returned page. Called with no args it
 * behaves exactly as before (whole queue) and keeps the same `["emailQueue"]`
 * cache key — prefix-match invalidation still refreshes every variant. */
export function useEmailQueue(params?: EmailQueueParams) {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: params ? ["emailQueue", params] : ["emailQueue"],
    queryFn: () => getEmailQueue(params),
    // Keep the PREVIOUS page's result on screen while a new page/filter is in
    // flight. Without this, `data` is undefined for a key with no cache entry,
    // so `total` fell back to 0 for one render — and a 0 total reads as
    // "1 page", which made the stale-page clamp in EmailWorkspace bounce a
    // freshly-clicked (uncached) page number back to page 1. It also kept the
    // "Showing X-Y of Z" text and empty states honest mid-transition.
    placeholderData: keepPreviousData,
    refetchInterval: 15_000,
  });

  return {
    emails: data?.emails ?? [],
    total: data?.total ?? 0,
    isLoading,
    isError,
    refetch,
  };
}
