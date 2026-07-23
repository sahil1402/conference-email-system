import apiClient from "./client";

import type {
  ApproveRequest,
  Email,
  EmailDetailResponse,
  EmailQueueResponse,
  EmailThreadResponse,
  IngestRequest,
  PipelineResult,
  QueueFacets,
  ReassignChairRequest,
  RerouteRequest,
  SendRequest,
  SendResponse,
} from "@/types";

/** Optional filters for the queue fetch. `lane` scopes to a routing lane
 * (e.g. "faq"); `chair_id` scopes to an assigned chair; `limit`/`offset`
 * paginate. Omitting all preserves the prior behavior (whole queue, backend
 * default page size). Server-side filtering + the lane/chair-scoped `total`
 * mean callers never derive counts/lists from a truncated page. */
export interface EmailQueueParams {
  lane?: string;
  chair_id?: number;
  /** Filter to emails with no assigned chair (assigned_chair_id IS NULL). */
  unassigned?: boolean;
  /** Lifecycle status exact-match (e.g. "DRAFT_GENERATED"). */
  status?: string;
  /** Ingestion source exact-match ("zendesk" | "toy_dataset"). */
  source?: string;
  /** Zendesk ticket status exact-match ("open" | "new" | …). */
  zendesk_status?: string;
  /** Case-insensitive match on subject OR sender. */
  search?: string;
  limit?: number;
  offset?: number;
}

/** Context filters for the facets aggregate — the queue params minus the facet
 * dimensions (source / zendesk_status) and pagination, so the bar/toggle counts
 * stay stable while a status/source is selected. */
export type QueueFacetsParams = Pick<
  EmailQueueParams,
  "lane" | "chair_id" | "unassigned" | "status" | "search"
>;

/** GET /emails/queue — fetch the email review queue (envelope with total + page_info).
 * `total` reflects the same (lane-filtered) query, so it is accurate regardless
 * of page size — use it for stats rather than counting the returned page. */
export async function getEmailQueue(
  params?: EmailQueueParams
): Promise<EmailQueueResponse> {
  const { data } = await apiClient.get<EmailQueueResponse>("/emails/queue", {
    params,
  });
  return data;
}

/** GET /emails/queue/facets — grouped counts for the status bar + source toggle.
 * A dedicated server-side aggregate (not a client tally over a capped page), so
 * counts include out-of-window rows. Honors the same context filters as the
 * queue so the facets compose with the active lane/chair/status/search. */
export async function getQueueFacets(
  params?: QueueFacetsParams
): Promise<QueueFacets> {
  const { data } = await apiClient.get<QueueFacets>("/emails/queue/facets", {
    params,
  });
  return data;
}

/** POST /emails/ingest — ingest a new email; returns the full pipeline result. */
export async function ingestEmail(data: IngestRequest): Promise<PipelineResult> {
  const { data: result } = await apiClient.post<PipelineResult>(
    "/emails/ingest",
    data
  );
  return result;
}

/** PATCH /emails/{id}/approve — approve an email (optionally with an edited draft). */
export async function approveEmail(
  id: number,
  data?: ApproveRequest
): Promise<Email> {
  // TODO: backend does not yet consume target_status - pending per-chair
  // OAuth send endpoint (Piece C)
  const { data: email } = await apiClient.patch<Email>(
    `/emails/${id}/approve`,
    data ?? {}
  );
  return email;
}

/** POST /emails/{id}/send — release the approved draft to the Zendesk ticket
 * (internal note by default; public reply needs ALLOW_AUTO_SEND). Returns the
 * updated email plus the send metadata. Gate/transport failures surface as the
 * normalized ApiError via the shared client interceptor. */
export async function sendEmail(
  id: number,
  data?: SendRequest
): Promise<SendResponse> {
  const { data: result } = await apiClient.post<SendResponse>(
    `/emails/${id}/send`,
    data ?? {}
  );
  return result;
}

/** PATCH /emails/{id}/reroute — move an email to a different lane. */
export async function rerouteEmail(
  id: number,
  data: RerouteRequest
): Promise<Email> {
  const { data: email } = await apiClient.patch<Email>(
    `/emails/${id}/reroute`,
    data
  );
  return email;
}

/** POST /emails/{id}/redraft — retry: re-run the full pipeline on this email. */
export async function retryEmail(
  id: number
): Promise<{ email_id: string; redrafting: boolean }> {
  const { data } = await apiClient.post<{ email_id: string; redrafting: boolean }>(
    `/emails/${id}/redraft`
  );
  return data;
}

/** PATCH /emails/{id}/reassign-chair — assign an email to a different chair (Phase 6A). */
export async function reassignChair(
  id: number,
  data: ReassignChairRequest
): Promise<Email> {
  const { data: email } = await apiClient.patch<Email>(
    `/emails/${id}/reassign-chair`,
    data
  );
  return email;
}

/** GET /emails/{id}/thread — the full multi-turn conversation for one ticket
 * (all turns incl. internal notes, oldest-first). Non-Zendesk emails return an
 * empty list. */
export async function getEmailThread(
  id: number
): Promise<EmailThreadResponse> {
  const { data } = await apiClient.get<EmailThreadResponse>(
    `/emails/${id}/thread`
  );
  return data;
}

/** GET /emails/by-ticket/{ticketId} — fetch one email (and its audit trail) by
 * its Zendesk ticket id. Same envelope as GET /emails/{email_id}. A 404 (no
 * email maps to the ticket id) rejects with the normalized ApiError via the
 * shared client interceptor, exactly like the other functions here. */
export async function getEmailByTicketId(
  ticketId: number | string
): Promise<EmailDetailResponse> {
  const { data } = await apiClient.get<EmailDetailResponse>(
    `/emails/by-ticket/${ticketId}`
  );
  return data;
}
