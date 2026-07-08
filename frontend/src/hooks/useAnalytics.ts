import { useQuery } from "@tanstack/react-query";

import {
  getActiveLearningCandidates,
  getAnalyticsSummary,
  getCalibration,
} from "@/lib/api";

/** Subscribe to the analytics summary (polls every 30s). */
export function useAnalytics() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["analytics"],
    queryFn: getAnalyticsSummary,
    refetchInterval: 30_000,
  });

  return { summary: data, isLoading, isError };
}

/** Fetch the calibration reliability report (static-ish; no polling needed). */
export function useCalibration() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["calibration"],
    queryFn: getCalibration,
  });

  return { calibration: data, isLoading, isError };
}

/** Fetch the active-learning candidate list (flagged emails for future labeling). */
export function useActiveLearningCandidates() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["activeLearning"],
    queryFn: getActiveLearningCandidates,
  });

  return {
    candidates: data?.candidates ?? [],
    total: data?.total ?? 0,
    isLoading,
    isError,
  };
}
