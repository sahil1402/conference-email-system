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

/** GET /emails/queue — fetch the email review queue (envelope with total + page_info). */
export async function getEmailQueue(): Promise<EmailQueueResponse> {
  const { data } = await apiClient.get<EmailQueueResponse>("/emails/queue");
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
