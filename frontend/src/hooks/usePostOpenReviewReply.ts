import { useMutation, useQueryClient } from "@tanstack/react-query";

import { postOpenReviewReply } from "@/lib/api";
import type {
  ApiError,
  PostOpenReviewReplyRequest,
  PostOpenReviewReplyResponse,
} from "@/types";

/**
 * Relay an approved reply to OpenReview.
 *
 * Kept out of `useEmailActions` on purpose. Every mutation there shares
 * `useInvalidateEmailQueries`, which refreshes the main queue, the analytics
 * aggregates, the Zendesk status facets and the thread — none of which this
 * action touches, and all of which would be needless traffic on a page that
 * shows none of them.
 *
 * What DOES need refreshing is the OpenReview queue (a posted reply leaves it,
 * per the server-side split in `/queue/openreview`) and this email's own detail
 * query — the row now carries `draft.openreview_post`, which is what the gate
 * reads to refuse a duplicate. Invalidating that is what makes a revisit show
 * the already-posted state instead of offering the button again.
 */
export function usePostOpenReviewReply() {
  const queryClient = useQueryClient();

  return useMutation<
    PostOpenReviewReplyResponse,
    ApiError,
    { id: number | string; data: PostOpenReviewReplyRequest }
  >({
    mutationFn: ({ id, data }) => postOpenReviewReply(id, data),
    onSuccess: (_result, { id }) => {
      queryClient.invalidateQueries({ queryKey: ["openReviewQueue"] });
      queryClient.invalidateQueries({ queryKey: ["emailById", String(id)] });
    },
  });
}
