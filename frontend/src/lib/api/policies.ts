import apiClient from "./client";

import type {
  ConflictReport,
  CreatePolicyRequest,
  EditPolicyRequest,
  PoliciesResponse,
  PolicyAuditResponse,
  PolicyDetail,
  PolicyDocument,
  PolicyListParams,
  SimilarResponse,
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
