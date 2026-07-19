import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createPolicy,
  findSimilarPolicies,
  getPolicy,
  listPolicies,
  listPolicyAudit,
  reactivatePolicy,
  reevaluatePolicies,
  retirePolicy,
} from "@/lib/api";
import type { CreatePolicyRequest, PolicyListParams } from "@/types";

/** Placeholder chair identity until the account system lands. */
export const ACTOR = "Chair1";

// --- Read: citation detail --------------------------------------------------

/**
 * Fetch one policy chunk's full detail by key, for the citation-detail popup.
 * Lazy: pass `null` (e.g. when the modal is closed) and the query stays idle.
 * Policy text is immutable in this read-only phase, so it caches indefinitely —
 * reopening the same citation is instant, no refetch.
 */
export function usePolicy(policyKey: string | null) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["policy", policyKey],
    queryFn: () => getPolicy(policyKey as string),
    enabled: policyKey != null,
    staleTime: Infinity,
  });

  return { policy: data ?? null, isLoading, isError };
}

// --- Read: KB browse + governance history -----------------------------------

function useInvalidateKb() {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: ["knowledgeBase"] });
    queryClient.invalidateQueries({ queryKey: ["policyAudit"] });
  };
}

export function usePolicies(params?: PolicyListParams) {
  const query = useQuery({
    queryKey: ["knowledgeBase", params],
    queryFn: () => listPolicies(params),
  });
  return {
    policies: query.data?.policies ?? [],
    isLoading: query.isLoading,
    isError: query.isError,
    refetch: query.refetch,
  };
}

export function usePolicyAudit() {
  const query = useQuery({ queryKey: ["policyAudit"], queryFn: () => listPolicyAudit() });
  return {
    entries: query.data?.entries ?? [],
    isLoading: query.isLoading,
    isError: query.isError,
    refetch: query.refetch,
  };
}

// --- Write: chair governance mutations --------------------------------------

export function useCreatePolicy() {
  const invalidate = useInvalidateKb();
  return useMutation({
    mutationFn: (data: CreatePolicyRequest) => createPolicy(data),
    onSuccess: invalidate,
  });
}

export function useRetirePolicy() {
  const invalidate = useInvalidateKb();
  return useMutation({
    mutationFn: (key: string) => retirePolicy(key, ACTOR),
    onSuccess: invalidate,
  });
}

export function useReactivatePolicy() {
  const invalidate = useInvalidateKb();
  return useMutation({
    mutationFn: (key: string) => reactivatePolicy(key, ACTOR),
    onSuccess: invalidate,
  });
}

export function useFindSimilar() {
  return useMutation({
    mutationFn: (body: { title: string; content: string }) => findSimilarPolicies(body),
  });
}

// --- Write: re-evaluate sweep ------------------------------------------------

/**
 * Trigger a re-draft sweep of open tickets. On success, invalidate the email
 * queue so any tickets flipping into "re-drafting…" (and their new drafts) show
 * up — the SSE stream also pushes these, this is the immediate nudge.
 */
export function useReevaluatePolicies() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => reevaluatePolicies(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["emailQueue"] });
    },
  });
}
