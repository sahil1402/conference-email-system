import { useQuery } from "@tanstack/react-query";

import { getEmailThread } from "@/lib/api";

/** Fetch a ticket's full thread (Piece T3): each message + all of its
 * per-message pipeline results (oldest-first), plus the latest-result id per
 * message. Backed by GET /emails/{id}/thread.
 *
 * Disabled until a valid `emailId` is provided, so it can be mounted on a detail
 * view before a row is selected. A non-Zendesk/toy email resolves to an empty
 * `messages` list, not an error. */
export function useEmailThread(emailId: number | null | undefined) {
  const enabled = typeof emailId === "number";
  const { data, isLoading, isError } = useQuery({
    queryKey: ["emailThread", emailId],
    queryFn: () => getEmailThread(emailId as number),
    enabled,
  });

  return {
    emailId: data?.email_id ?? null,
    messages: data?.messages ?? [],
    isLoading: enabled && isLoading,
    isError,
  };
}
