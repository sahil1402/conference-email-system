import apiClient from "./client";

import type {
  ApproveRequest,
  Email,
  EmailDetailResponse,
  EmailQueueResponse,
  EmailThreadResponse,
  IngestRequest,
  DismissOpenReviewCandidateRequest,
  DismissOpenReviewCandidateResponse,
  PipelineResult,
  PostOpenReviewReplyRequest,
  PostOpenReviewReplyResponse,
  QueueFacets,
  ReassignChairRequest,
  RerouteRequest,
  SendRequest,
  SendResponse,
  SetStatusRequest,
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
  /**
   * Inclusive lower bound on `received_at`, as a bare `YYYY-MM-DD` date.
   *
   * The backend documents that a bare date covers the WHOLE day
   * (`received_after` -> 00:00:00, `received_before` -> 23:59:59.999999) and
   * owns that expansion. Callers must NOT send a timestamp or do end-of-day
   * arithmetic here — doing so opts out of the server-side widening and
   * silently drops most of the final day. An inverted range is a 422.
   */
  received_after?: string;
  /** Inclusive upper bound on `received_at`, as a bare `YYYY-MM-DD` date. See
   *  `received_after` for the whole-day contract. */
  received_before?: string;
  limit?: number;
  offset?: number;
}

/**
 * Queue page size. Single source of truth for how many rows one page fetches;
 * pagination derives `offset = pageIndex * QUEUE_PAGE_SIZE` and the page count
 * from `total`. Kept ≤ the backend's `limit` cap (le=200).
 */
export const QUEUE_PAGE_SIZE = 100;

/** Context filters for the facets aggregate — the queue params minus the facet
 * dimensions (source / zendesk_status) and pagination, so the bar/toggle counts
 * stay stable while a status/source is selected.
 *
 * The date range belongs here, not among the facet dimensions: it is not a value
 * the status bar or source toggle renders, so narrowing to a window should
 * narrow the counts shown beside it, exactly as `status` and `search` do. The
 * backend applies it as a context filter for the same reason. */
export type QueueFacetsParams = Pick<
  EmailQueueParams,
  | "lane"
  | "chair_id"
  | "unassigned"
  | "status"
  | "search"
  | "received_after"
  | "received_before"
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

/** GET /emails/queue/openreview — the OpenReview-replies queue.
 *
 * The exact INVERSE of `getEmailQueue`: emails detected as a reviewer or author
 * replying to an OpenReview notification, still awaiting chair action. The
 * backend excludes them from `/emails/queue` and serves them here instead,
 * because the action is different (relay the reply onward rather than answer it
 * by email). The two are complements of one server-side predicate, so an email
 * is in exactly one of them.
 *
 * Deliberately reuses `EmailQueueParams` / `EmailQueueResponse` rather than
 * declaring parallel types: the backend routes take the same parameters in the
 * same order and return the same `{emails, total, page_info}` envelope from the
 * same serializer (verified against the routes, not assumed). A duplicate type
 * would be a second definition free to drift from the one the queue already
 * uses. If the two shapes ever genuinely diverge, split them THEN.
 *
 * `total` is the count for this queue's own filter set, so it is accurate
 * regardless of page size — same contract as `getEmailQueue`. */
export async function getOpenReviewQueue(
  params?: EmailQueueParams
): Promise<EmailQueueResponse> {
  const { data } = await apiClient.get<EmailQueueResponse>(
    "/emails/queue/openreview",
    { params }
  );
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

/** POST /emails/{id}/set-status — set the ticket's Zendesk status (new/open/
 * pending/solved) WITHOUT sending a reply. Returns the updated email plus the
 * status metadata. Transport/guard failures surface as the normalized ApiError. */
export async function setEmailStatus(
  id: number,
  status: SetStatusRequest["status"]
): Promise<SendResponse> {
  const { data: result } = await apiClient.post<SendResponse>(
    `/emails/${id}/set-status`,
    { status }
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

/** POST /emails/{id}/redraft — retry: re-run the full pipeline on this email.
 *
 * `forcedPolicyKey` (manual invoke) grounds the new draft on that policy in
 * addition to whatever retrieval ranks. Omitted → no request body at all, which
 * is byte-identical to the plain retry the backend has always accepted.
 */
export async function retryEmail(
  id: number,
  forcedPolicyKey?: string,
  excludedPolicyIds?: string[]
): Promise<{
  email_id: string;
  redrafting: boolean;
  forced_policy_key?: string | null;
  excluded_policy_ids?: string[] | null;
}> {
  // Only send a body when something was actually asked for — omitting it keeps
  // the plain retry byte-identical to what the backend has always accepted.
  const body: Record<string, unknown> = {};
  if (forcedPolicyKey) body.forced_policy_key = forcedPolicyKey;
  if (excludedPolicyIds?.length) body.excluded_policy_ids = excludedPolicyIds;

  const { data } = await apiClient.post<{
    email_id: string;
    redrafting: boolean;
    forced_policy_key?: string | null;
    excluded_policy_ids?: string[] | null;
  }>(
    `/emails/${id}/redraft`,
    Object.keys(body).length ? body : undefined
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
/** GET /emails/{id} — one email plus its audit trail, by PRIMARY KEY.
 *
 * The by-id sibling of `getEmailByTicketId`, returning the identical
 * `EmailDetailResponse`. It exists because a detail route keyed on `Email.id`
 * cannot use the by-ticket fetch: `zendesk_ticket_id` is nullable, so a
 * non-Zendesk row has no ticket to look up. Reaching for the queue list and
 * filtering client-side is the other alternative and is worse — it only finds
 * rows on the current page.
 *
 * 404 when no such row exists. A non-numeric id also 404s (the backend coerces
 * the path segment and treats an uncoercible one as not-found), so callers have
 * a single not-found case rather than the 404/422 split `/tickets/[ticketId]`
 * has to fold together. */
export async function getEmailById(
  emailId: number | string
): Promise<EmailDetailResponse> {
  const { data } = await apiClient.get<EmailDetailResponse>(
    `/emails/${emailId}`
  );
  return data;
}

export async function getEmailByTicketId(
  ticketId: number | string
): Promise<EmailDetailResponse> {
  const { data } = await apiClient.get<EmailDetailResponse>(
    `/emails/by-ticket/${ticketId}`
  );
  return data;
}

/** Relay a detected reply onward to OpenReview as a threaded Official Comment.
 *
 * `reply_text` is the chair's FINAL text and is posted verbatim — the backend
 * never falls back to the originally extracted string, so whatever sits in the
 * editor is what becomes visible on the forum.
 *
 * ⚠️ A 200 IS NOT UNCONDITIONALLY A FULL SUCCESS. The OpenReview comment is live
 * the moment this resolves, but the Zendesk auto-solve that follows can fail
 * independently — `ticket_resolution.outcome` says which of four things
 * happened. A caller that reads 200 as "all done" silently drops the
 * partial-success case, which is precisely the one that leaves a ticket open.
 *
 * Failure statuses, ALL of which mean nothing was posted:
 *  - 409 — refused by the post gate (not a candidate, no note id, or already
 *    posted). `detail.reason` says which.
 *  - 501 — OpenReview access or the venue id is not configured here.
 *  - 502 — OpenReview was reached and the call failed; `detail.error_type` names
 *    which (`OpenReviewNoteNotFoundError`, `OpenReviewPermissionError`,
 *    `OpenReviewThreadMismatchError`, `OpenReviewAPIError`).
 *  - 404 — no such email. ⚠️ This one's `detail` is a plain STRING, unlike the
 *    structured objects above.
 */
/** Record that a detected OpenReview reply is a FALSE POSITIVE of the detection.
 *
 * ⚠️ NOT A REROUTE, and deliberately a different endpoint from
 * `rerouteEmail`. That one changes the email's routing LANE and fires RL-bandit
 * and active-learning feedback keyed on the lane decision having been wrong.
 * Here the lane may have been perfectly correct — what was wrong is the
 * text-based detection that flagged the email as answering an OpenReview
 * notification. Routing is untouched; the email simply stops appearing in
 * `/queue/openreview` and resumes appearing in the main `/queue` under whatever
 * lane it already had.
 *
 * The flag is a dedicated column server-side, so the dismissal survives the
 * pipeline reprocessing that rewrites the extraction record.
 *
 * Failures:
 *  - 409 — never a candidate, so there is nothing to dismiss.
 *    `detail` is `{message, reason}`.
 *  - 422 — empty `reason` (the backend requires one).
 *  - 404 — no such email; ⚠️ this one's `detail` is a plain STRING.
 *
 * A second dismissal is NOT an error: the endpoint is idempotent and returns
 * 200 with `already_dismissed: true`.
 */
export async function dismissOpenReviewCandidate(
  emailId: number | string,
  payload: DismissOpenReviewCandidateRequest
): Promise<DismissOpenReviewCandidateResponse> {
  const { data } = await apiClient.post<DismissOpenReviewCandidateResponse>(
    `/emails/${emailId}/dismiss-openreview-candidate`,
    payload
  );
  return data;
}

export async function postOpenReviewReply(
  emailId: number | string,
  payload: PostOpenReviewReplyRequest
): Promise<PostOpenReviewReplyResponse> {
  const { data } = await apiClient.post<PostOpenReviewReplyResponse>(
    `/emails/${emailId}/post-openreview-reply`,
    payload
  );
  return data;
}
