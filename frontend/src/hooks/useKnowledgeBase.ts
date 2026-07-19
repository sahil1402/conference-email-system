import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createPolicy, findSimilarPolicies, listPolicies, listPolicyAudit,
  reactivatePolicy, retirePolicy,
} from "@/lib/api";
import type { CreatePolicyRequest, PolicyListParams } from "@/types";

/** Placeholder chair identity until the account system lands. */
export const ACTOR = "Chair1";

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
    isLoading: query.isLoading, isError: query.isError, refetch: query.refetch,
  };
}

export function usePolicyAudit() {
  const query = useQuery({ queryKey: ["policyAudit"], queryFn: () => listPolicyAudit() });
  return {
    entries: query.data?.entries ?? [],
    isLoading: query.isLoading, isError: query.isError, refetch: query.refetch,
  };
}

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
