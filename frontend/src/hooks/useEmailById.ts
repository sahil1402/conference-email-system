import { useQuery } from "@tanstack/react-query";

import { getEmailById } from "@/lib/api";
import type { ApiError, EmailDetailResponse } from "@/types";

/** Fetch one email (and its audit trail) by its primary key.
 *
 * The by-id counterpart of `useEmailByTicket`, and deliberately a separate hook
 * rather than a parameterised one: the two use different endpoints and must
 * hold different cache keys, or a row fetched by ticket would serve a request
 * for a different row fetched by id.
 *
 * Backs /openreview-replies/[id], whose rows are keyed on `Email.id` because
 * `zendesk_ticket_id` is nullable and a non-Zendesk candidate has none. `id`
 * null/empty disables the query. Polls on the same 15s cadence as the queues so
 * the detail stays live. */
export function useEmailById(emailId: string | number | null) {
  // Typed as the normalized ApiError the shared client interceptor rejects
  // with, so a caller can branch on `error.status` without casting.
  const { data, isLoading, isError, error, refetch } = useQuery<
    EmailDetailResponse,
    ApiError
  >({
    queryKey: ["emailById", String(emailId)],
    queryFn: () => getEmailById(emailId as string | number),
    enabled: emailId != null && emailId !== "",
    refetchInterval: 15_000,
    retry: false,
  });

  return {
    email: data?.email ?? null,
    auditTrail: data?.audit_trail ?? [],
    isLoading,
    isError,
    error,
    refetch,
  };
}
