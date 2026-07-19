import apiClient from "./client";

import type {
  CreatePolicyRequest,
  PoliciesResponse,
  PolicyAuditResponse,
  PolicyDetail,
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
export async function createPolicy(body: CreatePolicyRequest): Promise<{ policy_key: string; visibility: string; status: string }> {
  const { data } = await apiClient.post("/policies", body);
  return data;
}

/** PATCH /policies/{key}/retire. */
export async function retirePolicy(key: string, actor: string): Promise<{ policy_key: string; status: string }> {
  const { data } = await apiClient.patch(`/policies/${encodeURIComponent(key)}/retire`, { actor });
  return data;
}

/** PATCH /policies/{key}/reactivate. */
export async function reactivatePolicy(key: string, actor: string): Promise<{ policy_key: string; status: string }> {
  const { data } = await apiClient.patch(`/policies/${encodeURIComponent(key)}/reactivate`, { actor });
  return data;
}
