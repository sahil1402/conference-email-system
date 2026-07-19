import apiClient from "./client";

import type { PolicyDetail } from "@/types";

/**
 * Fetch one policy chunk's full detail by its key (e.g. `policy_117`), for the
 * citation-detail popup. The persisted email row does not carry retrieved
 * chunks, so the review UI only has the cited id — this resolves it to source,
 * tags, and full text. Read-only (GET /api/v1/policies/{key}); 404 on unknown
 * key surfaces as an axios error the caller/React Query handles.
 */
export async function getPolicy(policyKey: string): Promise<PolicyDetail> {
  const { data } = await apiClient.get<PolicyDetail>(
    `/policies/${encodeURIComponent(policyKey)}`
  );
  return data;
}
