import { useQuery } from "@tanstack/react-query";

import { getPolicy } from "@/lib/api";

/**
 * Fetch one policy chunk's full detail by key, for the citation-detail popup.
 * Lazy: pass `null` (e.g. when the modal is closed) and the query stays idle.
 * Policy text is immutable in this read-only phase, so it caches indefinitely —
 * reopening the same citation is instant, no refetch.
 */
export function usePolicy(policyKey: string | null) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["policy", policyKey],
    queryFn: () => getPolicy(policyKey as string),
    enabled: policyKey != null,
    staleTime: Infinity,
  });

  return { policy: data ?? null, isLoading, isError };
}
