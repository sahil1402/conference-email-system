import { useQuery } from "@tanstack/react-query";

import { getEmailThread } from "@/lib/api";

/** Fetch one ticket's conversation thread (all turns, oldest-first).
 *
 * Pass the selected email id; `null` disables the query (nothing selected).
 * Polls on the same 15s cadence as the queue so a freshly-ingested follow-up
 * turn appears without a manual refresh. */
export function useEmailThread(id: number | null) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["emailThread", id],
    queryFn: () => getEmailThread(id as number),
    enabled: id != null,
    refetchInterval: 15_000,
  });

  return {
    messages: data?.messages ?? [],
    isLoading,
    isError,
  };
}
