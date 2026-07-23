import { useMutation, useQueryClient } from "@tanstack/react-query";

import { approveEmail, ingestEmail, reassignChair, rerouteEmail, retryEmail, sendEmail } from "@/lib/api";
import type {
  ApiError,
  ApproveRequest,
  IngestRequest,
  PipelineResult,
  ReassignChairRequest,
  RerouteRequest,
  SendRequest,
  SendResponse,
} from "@/types";

/** Invalidate the queries affected by any email mutation. */
function useInvalidateEmailQueries() {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: ["emailQueue"] });
    queryClient.invalidateQueries({ queryKey: ["analytics"] });
    // The Zendesk-status facet counts (the "Solved / Closed" bucket total) and
    // the thread both shift when a reply is sent — a send moves the ticket's
    // bucket and appends our reply — so refresh them immediately instead of
    // waiting for their own 15s poll.
    queryClient.invalidateQueries({ queryKey: ["queueFacets"] });
    queryClient.invalidateQueries({ queryKey: ["emailThread"] });
    // The /tickets/[ticketId] detail pane reads the same email through its own
    // ["emailByTicket", ticketId] query, so it must refresh on the same actions
    // as the queue — otherwise the standalone ticket view only updates on its
    // 15s poll. A prefix invalidation (no id) mirrors ["emailThread"] above:
    // the mutations key off email id, not ticket id, and only the currently
    // viewed ticket's query is ever active, so this is effectively scoped.
    queryClient.invalidateQueries({ queryKey: ["emailByTicket"] });
  };
}

/** Approve an email (optionally with an edited draft). */
export function useApproveEmail() {
  const invalidate = useInvalidateEmailQueries();
  return useMutation({
    mutationFn: ({ id, data }: { id: number; data?: ApproveRequest }) =>
      approveEmail(id, data),
    onSuccess: invalidate,
  });
}

/** Release an approved draft to the Zendesk ticket (internal note or public reply). */
export function useSendEmail() {
  const invalidate = useInvalidateEmailQueries();
  return useMutation<SendResponse, ApiError, { id: number; data?: SendRequest }>({
    mutationFn: ({ id, data }) => sendEmail(id, data),
    onSuccess: invalidate,
  });
}

/** Reroute an email to a different lane. */
export function useRerouteEmail() {
  const invalidate = useInvalidateEmailQueries();
  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: RerouteRequest }) =>
      rerouteEmail(id, data),
    onSuccess: invalidate,
  });
}

/** Retry: re-run the full pipeline on an email and overwrite its draft. */
export function useRetryEmail() {
  const invalidate = useInvalidateEmailQueries();
  return useMutation({
    mutationFn: (id: number) => retryEmail(id),
    onSuccess: invalidate,
  });
}

/** Reassign an email to a different chair (Phase 6A). */
export function useReassignChair() {
  const invalidate = useInvalidateEmailQueries();
  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: ReassignChairRequest }) =>
      reassignChair(id, data),
    onSuccess: invalidate,
  });
}

/** Ingest a new email through the pipeline (returns the PipelineResult). */
export function useIngestEmail() {
  const invalidate = useInvalidateEmailQueries();
  return useMutation<PipelineResult, ApiError, IngestRequest>({
    mutationFn: (data: IngestRequest) => ingestEmail(data),
    onSuccess: invalidate,
  });
}
