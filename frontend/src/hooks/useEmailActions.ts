import { useMutation, useQueryClient } from "@tanstack/react-query";

import { approveEmail, ingestEmail, rerouteEmail } from "@/lib/api";
import type {
  ApiError,
  ApproveRequest,
  IngestRequest,
  PipelineResult,
  RerouteRequest,
} from "@/types";

/** Invalidate the queries affected by any email mutation. */
function useInvalidateEmailQueries() {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: ["emailQueue"] });
    queryClient.invalidateQueries({ queryKey: ["analytics"] });
  };
}

/** Approve an email (optionally with an edited draft). */
export function useApproveEmail() {
  const invalidate = useInvalidateEmailQueries();
  return useMutation({
    mutationFn: ({ id, data }: { id: number; data?: ApproveRequest }) =>
      approveEmail(id, data),
    onSuccess: invalidate,
  });
}

/** Reroute an email to a different lane. */
export function useRerouteEmail() {
  const invalidate = useInvalidateEmailQueries();
  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: RerouteRequest }) =>
      rerouteEmail(id, data),
    onSuccess: invalidate,
  });
}

/** Ingest a new email through the pipeline (returns the PipelineResult). */
export function useIngestEmail() {
  const invalidate = useInvalidateEmailQueries();
  return useMutation<PipelineResult, ApiError, IngestRequest>({
    mutationFn: (data: IngestRequest) => ingestEmail(data),
    onSuccess: invalidate,
  });
}
