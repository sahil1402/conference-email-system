import apiClient from "./client";

import type { AuditEntry } from "@/types";

/**
 * Raw item shape from GET /api/v1/audit (audit.py::AuditLogResponse) — the
 * paginated audit feed, which unlike /analytics/recent-activity DOES carry the
 * row id and the per-action ``details`` (needed for the chair-edit diff view).
 */
interface AuditApiItem {
  id: number;
  email_id: number;
  action: string;
  actor: string;
  details: Record<string, unknown> | null;
  created_at: string | null;
}

interface AuditApiPage {
  items: AuditApiItem[];
  total: number;
  limit: number;
  offset: number;
}

/**
 * Fetch the audit feed (newest-first) and normalize it into AuditEntry[].
 * Reads the real /audit route so ``details`` (e.g. an approved-with-edits diff)
 * is available to the UI.
 */
export async function getAuditLog(): Promise<AuditEntry[]> {
  const { data } = await apiClient.get<AuditApiPage>("/audit", {
    params: { limit: 100 },
  });
  return data.items.map((item) => ({
    id: item.id,
    email_id: item.email_id,
    action: item.action,
    actor: item.actor,
    details: item.details ?? {},
    created_at: item.created_at ?? "",
  }));
}
