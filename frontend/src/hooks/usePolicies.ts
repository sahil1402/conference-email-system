import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  acceptSuggestion,
  createPolicy,
  editPolicy,
  findSimilarPolicies,
  getPolicy,
  listPolicies,
  listPolicyAudit,
  listSuggestions,
  reactivatePolicy,
  recheckPolicy,
  reevaluatePolicies,
  rejectSuggestion,
  retirePolicy,
  revertPolicyEdit,
  suggestionsCount,
} from "@/lib/api";
import type { CreatePolicyRequest, EditPolicyRequest, PolicyListParams } from "@/types";

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

export function usePolicies(
  params?: PolicyListParams,
  options?: { enabled?: boolean },
) {
  // `enabled` defaults to true, so every existing caller is unchanged. The
  // policy picker uses it to stay silent until the chair has typed something —
  // without it an empty search box would fetch the entire KB on open.
  const enabled = options?.enabled ?? true;
  const query = useQuery({
    queryKey: ["knowledgeBase", params],
    queryFn: () => listPolicies(params),
    enabled,
  });
  return {
    policies: query.data?.policies ?? [],
    // A disabled query is "pending" forever in react-query v5; report it as not
    // loading so callers don't render a spinner for a query that never runs.
    isLoading: enabled && query.isLoading,
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

export function useEditPolicy() {
  const invalidate = useInvalidateKb();
  return useMutation({
    mutationFn: ({ key, body }: { key: string; body: Omit<EditPolicyRequest, "actor"> }) =>
      editPolicy(key, { ...body, actor: ACTOR }),
    onSuccess: invalidate,
  });
}

export function useRevertPolicyEdit() {
  const invalidate = useInvalidateKb();
  return useMutation({
    mutationFn: (key: string) => revertPolicyEdit(key, ACTOR),
    onSuccess: invalidate,
  });
}

export function useFindSimilar() {
  return useMutation({
    mutationFn: (body: { title: string; content: string }) => findSimilarPolicies(body),
  });
}

/** Recompute a policy's conflict report on demand (the per-card Re-check). */
export function useRecheckPolicy() {
  const invalidate = useInvalidateKb();
  return useMutation({
    mutationFn: (key: string) => recheckPolicy(key),
    onSuccess: invalidate,
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

// --- Continual Experience Learning: suggestions review (Task 6) -------------

/**
 * CEL chair-review queue. `status` defaults to "pending" (the review view);
 * pass `null` for every status. `enabled` mirrors usePolicies — lets a caller
 * defer the fetch until its view is actually showing (e.g. the Suggestions
 * segment on /knowledge-base).
 */
export function useSuggestions(
  status: string | null = "pending",
  options?: { enabled?: boolean },
) {
  const enabled = options?.enabled ?? true;
  const query = useQuery({
    queryKey: ["suggestions", status],
    queryFn: () => listSuggestions(status),
    enabled,
  });
  return {
    suggestions: query.data?.suggestions ?? [],
    isLoading: enabled && query.isLoading,
    isError: query.isError,
    refetch: query.refetch,
  };
}

/** Pending-suggestion count for the chair-nav badge. Always enabled (cheap,
 *  and the badge should show regardless of which KB segment is active). */
export function useSuggestionsCount() {
  const query = useQuery({ queryKey: ["suggestions", "count"], queryFn: () => suggestionsCount() });
  return {
    pending: query.data?.pending ?? 0,
    isLoading: query.isLoading,
  };
}

function useInvalidateSuggestions() {
  const queryClient = useQueryClient();
  return () => {
    // A suggestion's disposition can move the KB (accept) or just leave the
    // review queue (reject) — invalidate all three so every consumer
    // (the policy list, the audit log, and this review queue/badge) is
    // consistent regardless of which mutation fired.
    queryClient.invalidateQueries({ queryKey: ["knowledgeBase"] });
    queryClient.invalidateQueries({ queryKey: ["policyAudit"] });
    queryClient.invalidateQueries({ queryKey: ["suggestions"] });
  };
}

export function useRejectSuggestion() {
  const invalidate = useInvalidateSuggestions();
  return useMutation({
    mutationFn: ({ id, reason }: { id: number; reason?: string | null }) =>
      rejectSuggestion(id, { actor: ACTOR, reason }),
    onSuccess: invalidate,
  });
}

/**
 * Links a suggestion to the policy it produced. Call this AFTER
 * useCreatePolicy's create succeeds (this hook never creates a policy itself
 * — see acceptSuggestion's own doc comment).
 */
export function useAcceptSuggestion() {
  const invalidate = useInvalidateSuggestions();
  return useMutation({
    mutationFn: ({ id, policyKey }: { id: number; policyKey: string }) =>
      acceptSuggestion(id, { actor: ACTOR, policy_key: policyKey }),
    onSuccess: invalidate,
  });
}
