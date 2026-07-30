import apiClient from "./client";

import type {
  AcceptSuggestionRequest,
  AcceptSuggestionResponse,
  ConflictReport,
  CreatePolicyRequest,
  EditPolicyRequest,
  PoliciesResponse,
  PolicyAuditResponse,
  PolicyDetail,
  PolicyDocument,
  PolicyListParams,
  RejectSuggestionRequest,
  RejectSuggestionResponse,
  SimilarResponse,
  SuggestionsCountResponse,
  SuggestionsResponse,
} from "@/types";

// --- Read: citation detail --------------------------------------------------

/**
 * Fetch one policy chunk's full detail by its key (e.g. `policy_117`), for the
 * citation-detail popup. The persisted email row does not carry retrieved
 * chunks, so the review UI only has the cited id — this resolves it to source,
 * tags, and full text. Read-only (GET /api/v1/policies/{key}); 404 on unknown
 * key surfaces as an axios error the caller/React Query handles.
 */
export async function getPolicy(policyKey: string): Promise<PolicyDetail> {
  const { data } = await apiClient.get<PolicyDetail>(
    `/policies/${encodeURIComponent(policyKey)}`,
  );
  return data;
}

// --- Read: KB browse + governance history -----------------------------------

/** GET /policies — filtered KB browse. */
export async function listPolicies(params?: PolicyListParams): Promise<PoliciesResponse> {
  const { data } = await apiClient.get<PoliciesResponse>("/policies", { params });
  return data;
}

/** GET /policies/audit — governance history, newest first. */
export async function listPolicyAudit(params?: { limit?: number; offset?: number }): Promise<PolicyAuditResponse> {
  const { data } = await apiClient.get<PolicyAuditResponse>("/policies/audit", { params });
  return data;
}

/** POST /policies/similar — related existing policies for the override assist. */
export async function findSimilarPolicies(body: { title: string; content: string }): Promise<SimilarResponse> {
  const { data } = await apiClient.post<SimilarResponse>("/policies/similar", body);
  return data;
}

// --- Write: chair governance ------------------------------------------------

/** POST /policies — create an internal policy (optionally retiring superseded keys). */
export async function createPolicy(
  body: CreatePolicyRequest,
): Promise<{ policy_key: string; visibility: string; status: string; conflict_report?: ConflictReport | null }> {
  const { data } = await apiClient.post("/policies", body);
  return data;
}

/** PATCH /policies/{key}/retire. */
export async function retirePolicy(key: string, actor: string): Promise<{ policy_key: string; status: string }> {
  const { data } = await apiClient.patch(`/policies/${encodeURIComponent(key)}/retire`, { actor });
  return data;
}

/** PATCH /policies/{key}/reactivate. */
export async function reactivatePolicy(
  key: string, actor: string,
): Promise<{ policy_key: string; status: string; conflict_report?: ConflictReport | null }> {
  const { data } = await apiClient.patch(`/policies/${encodeURIComponent(key)}/reactivate`, { actor });
  return data;
}

/** POST /policies/{key}/recheck — recompute + persist this policy's conflict report (2e). */
export async function recheckPolicy(
  key: string,
): Promise<{ policy_key: string; conflict_report: ConflictReport | null }> {
  const { data } = await apiClient.post(`/policies/${encodeURIComponent(key)}/recheck`);
  return data;
}

/** PATCH /policies/{key}/edit — edit an active policy into a new version. */
export async function editPolicy(
  key: string,
  body: EditPolicyRequest,
): Promise<PolicyDocument> {
  const { data } = await apiClient.patch<PolicyDocument>(
    `/policies/${encodeURIComponent(key)}/edit`,
    body,
  );
  return data;
}

/** POST /policies/{key}/revert-edit — undo one edit (restore prior version). */
export async function revertPolicyEdit(
  key: string,
  actor: string,
): Promise<PolicyDocument> {
  const { data } = await apiClient.post<PolicyDocument>(
    `/policies/${encodeURIComponent(key)}/revert-edit`,
    { actor },
  );
  return data;
}

// --- Write: re-evaluate sweep ------------------------------------------------

/** Response of POST /policies/reevaluate. */
export interface ReevaluateResponse {
  open: number;
  scheduled: boolean;
}

/**
 * Trigger one background re-draft sweep of the open tickets after KB edits.
 * Returns immediately with the open-ticket count; the sweep runs server-side.
 */
export async function reevaluatePolicies(): Promise<ReevaluateResponse> {
  const { data } = await apiClient.post<ReevaluateResponse>("/policies/reevaluate");
  return data;
}

// --- Continual Experience Learning: suggestions review (Task 6) -------------

/**
 * GET /policies/suggestions — CEL chair-review queue. `status` defaults to
 * the pending queue (matches the endpoint's own default); pass `null` to omit
 * the filter and fetch every status.
 */
export async function listSuggestions(status: string | null = "pending"): Promise<SuggestionsResponse> {
  const { data } = await apiClient.get<SuggestionsResponse>("/policies/suggestions", {
    params: status == null ? undefined : { status },
  });
  return data;
}

/** GET /policies/suggestions/count — pending count for the nav badge. */
export async function suggestionsCount(): Promise<SuggestionsCountResponse> {
  const { data } = await apiClient.get<SuggestionsCountResponse>("/policies/suggestions/count");
  return data;
}

/** PATCH /policies/suggestions/{id}/reject. */
export async function rejectSuggestion(
  id: number,
  body: RejectSuggestionRequest,
): Promise<RejectSuggestionResponse> {
  const { data } = await apiClient.patch<RejectSuggestionResponse>(
    `/policies/suggestions/${id}/reject`,
    body,
  );
  return data;
}

/**
 * PATCH /policies/suggestions/{id}/accept — links a suggestion to the policy
 * it produced. Does NOT create the policy itself; call after the existing
 * `POST /policies` create succeeds (see useCreatePolicy/useAcceptSuggestion).
 */
export async function acceptSuggestion(
  id: number,
  body: AcceptSuggestionRequest,
): Promise<AcceptSuggestionResponse> {
  const { data } = await apiClient.patch<AcceptSuggestionResponse>(
    `/policies/suggestions/${id}/accept`,
    body,
  );
  return data;
}
