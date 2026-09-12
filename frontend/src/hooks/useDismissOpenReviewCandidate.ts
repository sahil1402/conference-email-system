import { useMutation, useQueryClient } from "@tanstack/react-query";

import { dismissOpenReviewCandidate } from "@/lib/api";
import type {
  ApiError,
  DismissOpenReviewCandidateRequest,
  DismissOpenReviewCandidateResponse,
} from "@/types";

/**
 * Record that a detected OpenReview reply is a false positive.
 *
 * Invalidates BOTH queues, unlike `usePostOpenReviewReply` which only touches
 * the OpenReview one. A dismissal is the one action that moves an email ACROSS
 * the split: it leaves `/queue/openreview` and appears in the main `/queue`
 * under the lane it already had. Refreshing only the queue being left would
 * leave the Inbox to discover the arrival on its own 15s poll.
 *
 * `queueFacets` goes too — the source/status counts beside the main queue are
 * computed over the same filtered set, so they shift by one as the email
 * arrives.
 */
export function useDismissOpenReviewCandidate() {
  const queryClient = useQueryClient();

  return useMutation<
    DismissOpenReviewCandidateResponse,
    ApiError,
    { id: number | string; data: DismissOpenReviewCandidateRequest }
  >({
    mutationFn: ({ id, data }) => dismissOpenReviewCandidate(id, data),
    onSuccess: (_result, { id }) => {
      queryClient.invalidateQueries({ queryKey: ["openReviewQueue"] });
      queryClient.invalidateQueries({ queryKey: ["emailQueue"] });
      queryClient.invalidateQueries({ queryKey: ["queueFacets"] });
      queryClient.invalidateQueries({ queryKey: ["emailById", String(id)] });
    },
  });
}
