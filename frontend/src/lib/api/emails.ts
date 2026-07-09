import apiClient from "./client";

import type {
  ApproveRequest,
  Email,
  EmailQueueResponse,
  IngestRequest,
  PipelineResult,
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
  /** Case-insensitive match on subject OR sender. */
  search?: string;
  limit?: number;
  offset?: number;
}

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
