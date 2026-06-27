import { useQuery } from "@tanstack/react-query";

import { getEmailQueue } from "@/lib/api";

/** Subscribe to the email review queue (polls every 15s). */
export function useEmailQueue() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["emailQueue"],
    queryFn: getEmailQueue,
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
