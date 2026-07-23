import { useQuery } from "@tanstack/react-query";

import { getEmailByTicketId } from "@/lib/api";
import type { ApiError, EmailDetailResponse } from "@/types";

/** Fetch one email (and its audit trail) by its Zendesk ticket id.
 *
 * Backs the /tickets/[ticketId] route: unlike the queue detail pane (which
 * reads the selected row out of the already-loaded list), this uses the
 * dedicated GET /emails/by-ticket/{id} endpoint so a ticket URL resolves even
 * when the row isn't on the current queue page. `ticketId` null disables the
 * query. Polls on the same 15s cadence as the queue so the detail stays live. */
export function useEmailByTicket(ticketId: string | number | null) {
  // Error is typed as the normalized ApiError ({ detail, status }) the shared
  // client interceptor rejects with, so callers can branch on `error.status`
  // (e.g. 404 → not-found vs 5xx → generic error) without casting.
  const { data, isLoading, isError, error, refetch } = useQuery<
    EmailDetailResponse,
    ApiError
  >({
    queryKey: ["emailByTicket", String(ticketId)],
    queryFn: () => getEmailByTicketId(ticketId as string | number),
    enabled: ticketId != null && ticketId !== "",
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
