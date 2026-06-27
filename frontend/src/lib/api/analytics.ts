import apiClient from "./client";

import type { AnalyticsSummary } from "@/types";

/** GET /analytics/summary — dashboard summary metrics. */
export async function getAnalyticsSummary(): Promise<AnalyticsSummary> {
  const { data } = await apiClient.get<AnalyticsSummary>("/analytics/summary");
  return data;
}
