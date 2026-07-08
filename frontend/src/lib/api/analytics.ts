import apiClient from "./client";

import type { AnalyticsSummary, CalibrationReport } from "@/types";

/** GET /analytics/summary — dashboard summary metrics. */
export async function getAnalyticsSummary(): Promise<AnalyticsSummary> {
  const { data } = await apiClient.get<AnalyticsSummary>("/analytics/summary");
  return data;
}

/** GET /analytics/calibration — reliability-diagram data (raw + calibrated). */
export async function getCalibration(): Promise<CalibrationReport> {
  const { data } = await apiClient.get<CalibrationReport>("/analytics/calibration");
  return data;
}
