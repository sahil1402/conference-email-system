import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { getOpenReviewQueue, type EmailQueueParams } from "@/lib/api";

/** Subscribe to the OpenReview-replies queue (polls every 15s).
 *
 * Deliberately mirrors `useEmailQueue` rather than generalising it: the two
 * queues are complements of one server-side predicate and take the same
 * parameters, so they behave identically here — but they need SEPARATE cache
 * keys, or relaying a reply would invalidate one list and leave the other
 * showing the row it just removed.
 *
 * `placeholderData: keepPreviousData` is carried over for the same reason it
 * exists there, and it is not optional politeness: without it `data` is
 * undefined for a key with no cache entry, so `total` falls back to 0 for one
 * render — and a 0 total reads as "1 page", which bounces a freshly-clicked
 * (uncached) page back to page 1. It also keeps the empty state honest
 * mid-transition, so paging never flashes "no candidates". */
export function useOpenReviewQueue(params?: EmailQueueParams) {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: params ? ["openReviewQueue", params] : ["openReviewQueue"],
    queryFn: () => getOpenReviewQueue(params),
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
