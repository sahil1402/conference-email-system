import apiClient from "./client";

import type { Chair, ReassignmentEvent } from "@/types";

/** GET /api/v1/chairs response envelope (chairs.py::list_chairs). */
interface ChairsResponse {
  chairs: Chair[];
  total: number;
}

/**
 * Fetch the chair roster. Pass `activeOnly` to get only chairs that can be
 * auto-routed to (the reassignment picker uses the full list so a human can
 * still hand an email to an inactive chair as an override).
 */
export async function getChairs(activeOnly = false): Promise<Chair[]> {
  const { data } = await apiClient.get<ChairsResponse>("/chairs", {
    params: activeOnly ? { active_only: true } : undefined,
  });
  return data.chairs;
}

interface AuditItem {
  email_id: number;
  action: string;
  details: Record<string, unknown> | null;
  created_at: string | null;
}
interface AuditPage {
  items: AuditItem[];
  total: number;
}

/**
 * Fetch chair-reassignment events for analytics, derived from the audit feed
 * (GET /audit?action=chair_reassigned). No dedicated backend endpoint — the
 * reassignment audit entry carries original_chair_id / new_chair_id in details.
 */
export async function getReassignmentEvents(): Promise<ReassignmentEvent[]> {
  const { data } = await apiClient.get<AuditPage>("/audit", {
    params: { action: "chair_reassigned", limit: 200 },
  });
  return data.items.map((it) => {
    const d = it.details ?? {};
    return {
      email_id: it.email_id,
      original_chair_id:
        typeof d.original_chair_id === "number" ? d.original_chair_id : null,
      new_chair_id: typeof d.new_chair_id === "number" ? d.new_chair_id : null,
      at: it.created_at,
    };
  });
}
