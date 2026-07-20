import apiClient from "./client";

import type {
  ApproveRequest,
  Email,
  EmailQueueResponse,
  IngestRequest,
  PipelineResult,
  QueueFacets,
  ReassignChairRequest,
  RerouteRequest,
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
  const { data: email } = await apiClient.patch<Email>(
    `/emails/${id}/approve`,
    data ?? {}
  );
  return email;
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
