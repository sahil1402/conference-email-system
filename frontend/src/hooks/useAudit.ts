import { useQuery } from "@tanstack/react-query";

import { getAuditLog } from "@/lib/api";

/** Subscribe to the audit feed (10s stale time). */
export function useAudit() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["audit"],
    queryFn: getAuditLog,
    staleTime: 10_000,
  });

  return { entries: data ?? [], isLoading, isError };
}
