import apiClient from "./client";

import type { AuditEntry } from "@/types";

/**
 * Raw item shape from GET /analytics/recent-activity — the backend's only
 * cross-email audit feed (20 most recent actions). There is no GET /audit, and
 * this feed omits the row id and the metadata column.
 */
interface RecentActivityItem {
  email_id: string;
  action: string;
  actor: string;
  timestamp: string | null;
}

/**
 * Fetch the audit feed and normalize it into AuditEntry[].
 *
 * Wired to /analytics/recent-activity because the backend exposes no /audit
 * route (see types.AuditEntry). `id` falls back to the feed index and `details`
 * is empty since recent-activity doesn't return per-action metadata.
 */
export async function getAuditLog(): Promise<AuditEntry[]> {
  const { data } = await apiClient.get<RecentActivityItem[]>(
    "/analytics/recent-activity"
  );
  return data.map((item, i) => ({
    id: i,
    email_id: Number(item.email_id),
    action: item.action,
    actor: item.actor,
    details: {},
    created_at: item.timestamp ?? "",
  }));
}
