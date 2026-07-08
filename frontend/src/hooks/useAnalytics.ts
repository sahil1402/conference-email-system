import { useQuery } from "@tanstack/react-query";

import { getAnalyticsSummary, getCalibration } from "@/lib/api";

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
