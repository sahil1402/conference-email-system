"use client";

import { useEffect, useMemo, useState } from "react";
import { Inbox, SearchX } from "lucide-react";

import { useEmailQueue } from "@/hooks/useEmailQueue";
import { useEmailQueueStream } from "@/hooks/useEmailQueueStream";
import { useQueueFacets } from "@/hooks/useQueueFacets";
import type { EmailQueueParams, QueueFacetsParams } from "@/lib/api";
import {
  useApproveEmail,
  useRerouteEmail,
  useReassignChair,
  useRetryEmail,
} from "@/hooks/useEmailActions";
import { useChairs } from "@/hooks/useChairs";
import { useAppConfig } from "@/hooks/useAppConfig";
import {
  EmailListItem,
  EmailDetail,
  EmailFilters,
  SourceToggle,
  ZendeskStatusBar,
} from "@/components/email";
import {
  Badge,
  EmptyState,
  ErrorBanner,
  LiveStatusDot,
  LoadingSpinner,
} from "@/components/ui";

type LaneFilter = "all" | "faq" | "human_review";

export default function QueuePage() {
  const { status: streamStatus } = useEmailQueueStream();
  const { mutate: approve, isPending: isApproving } = useApproveEmail();
  const { mutate: reroute, isPending: isRerouting } = useRerouteEmail();
  const { mutateAsync: reassignChairAsync, isPending: isReassigning } =
    useReassignChair();
  const { mutate: retry } = useRetryEmail();
  const { allowAutoSend } = useAppConfig();
  const { chairs, byId: chairsById } = useChairs();

  const [selectedEmailId, setSelectedEmailId] = useState<number | null>(null);
  const [search, setSearch] = useState("");
  const [laneFilter, setLaneFilter] = useState<LaneFilter>("all");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [chairFilter, setChairFilter] = useState<string>("all");
  // Zendesk-specific filters. `sourceFilter` self-hides when only one source
  // exists; `zendeskStatusFilter` is null when no status is selected.
  const [sourceFilter, setSourceFilter] = useState<string>("all");
  const [zendeskStatusFilter, setZendeskStatusFilter] = useState<string | null>(
    null
  );

  // Debounce the search box so typing doesn't fire a request per keystroke.
  const [debouncedSearch, setDebouncedSearch] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search.trim()), 250);
    return () => clearTimeout(t);
  }, [search]);

  // Every filter (lane / status / search / chair / unassigned) is applied
  // SERVER-SIDE, so `emails` is the full matching set and `total` its true count
  // — not a client-side slice of a capped 20-row page (the bug that made lane /
  // status / search / chair filters drop out-of-window matches). The queue is
  // small, so one page of 200 covers the whole result.
  // Shared context (everything EXCEPT the facet dimensions source/zendesk_status)
  // — reused for the queue fetch and, on its own, for the facet counts so the
  // bar/toggle compose with these filters yet stay stable while a status/source
  // is selected.
  const contextParams = useMemo<QueueFacetsParams>(() => {
    const params: QueueFacetsParams = {};
    if (laneFilter !== "all") params.lane = laneFilter;
    if (statusFilter !== "all") params.status = statusFilter;
    if (debouncedSearch) params.search = debouncedSearch;
    if (chairFilter === "unassigned") params.unassigned = true;
    else if (chairFilter !== "all") params.chair_id = Number(chairFilter);
    return params;
  }, [laneFilter, statusFilter, debouncedSearch, chairFilter]);

  const queueParams = useMemo<EmailQueueParams>(() => {
    const params: EmailQueueParams = { ...contextParams, limit: 200 };
    if (sourceFilter !== "all") params.source = sourceFilter;
    if (zendeskStatusFilter) params.zendesk_status = zendeskStatusFilter;
    return params;
  }, [contextParams, sourceFilter, zendeskStatusFilter]);
  const { emails, total, isLoading, isError, refetch } =
    useEmailQueue(queueParams);

  // Facet counts for the status bar + source toggle (dedicated aggregate).
  const { byZendeskStatus, sources } = useQueueFacets(contextParams);

  // The status bar is only meaningful for Zendesk rows — show it when the source
  // selection would include Zendesk and there are Zendesk-status counts.
  const showStatusBar =
    sourceFilter !== "toy_dataset" && Object.keys(byZendeskStatus).length > 0;

  const selectedEmail =
    selectedEmailId == null
      ? null
      : emails.find((e) => e.id === selectedEmailId) ?? null;

  return (
    <div className="flex h-screen overflow-hidden">
      {/* LEFT PANE */}
      <aside
        className="flex w-80 shrink-0 flex-col"
        style={{ borderRight: "1px solid var(--border)" }}
      >
        <div className="space-y-4 p-4" style={{ borderBottom: "1px solid var(--border-subtle)" }}>
          <div className="flex items-center gap-2">
            <h1
              className="text-lg font-semibold tracking-tight"
              style={{ color: "var(--text-primary)" }}
            >
              Email Queue
            </h1>
            <Badge variant="neutral" size="sm">
              {total}
            </Badge>
            <span className="ml-auto">
              <LiveStatusDot status={streamStatus} />
            </span>
          </div>
          <EmailFilters
            search={search}
            onSearchChange={setSearch}
            laneFilter={laneFilter}
            onLaneChange={setLaneFilter}
            statusFilter={statusFilter as "all" | "PENDING" | "DRAFT_GENERATED" | "APPROVED"}
            onStatusChange={setStatusFilter}
            chairs={chairs}
            chairFilter={chairFilter}
            onChairChange={setChairFilter}
          />
          {/* Source toggle — self-hides unless ≥2 distinct sources exist. */}
          <SourceToggle
            sources={sources}
            value={sourceFilter}
            onChange={(v) => {
              setSourceFilter(v);
              // A zendesk_status filter is meaningless once we scope to
              // toy_dataset — clear it so the queue isn't silently emptied.
              if (v === "toy_dataset") setZendeskStatusFilter(null);
            }}
          />
          {/* Zendesk status bar — composes with lane / chair / search above. */}
          {showStatusBar && (
            <ZendeskStatusBar
              counts={byZendeskStatus}
              selected={zendeskStatusFilter}
              onSelect={setZendeskStatusFilter}
            />
          )}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {isLoading ? (
            <div className="flex items-center justify-center py-20">
              <LoadingSpinner size="lg" />
            </div>
          ) : isError ? (
            <div className="p-4">
              <ErrorBanner
                message="Couldn't load the email queue."
                onRetry={() => refetch()}
              />
            </div>
          ) : total === 0 ? (
            <EmptyState
              icon={<SearchX className="h-5 w-5" />}
              title="No emails match your filters"
              description="Try clearing the search or switching lane / status filters."
            />
          ) : (
            <ul>
              {emails.length < total && (
                <li
                  className="px-4 py-2 text-xs"
                  style={{ color: "var(--text-muted)" }}
                >
                  Showing {emails.length} of {total} — refine filters to narrow.
                </li>
              )}
              {emails.map((email) => (
                <li
                  key={email.id}
                  style={{ borderBottom: "1px solid var(--border-subtle)" }}
                >
                  <EmailListItem
                    email={email}
                    isSelected={email.id === selectedEmailId}
                    onClick={() => setSelectedEmailId(email.id)}
                    chairName={
                      email.assigned_chair_id != null
                        ? chairsById.get(email.assigned_chair_id)?.name ?? null
                        : null
                    }
                  />
                </li>
              ))}
            </ul>
          )}
        </div>
      </aside>

      {/* RIGHT PANE */}
      <section className="min-w-0 flex-1 overflow-hidden">
        {selectedEmail ? (
          <EmailDetail
            key={selectedEmail.id}
            email={selectedEmail}
            isApproving={isApproving}
            isRerouting={isRerouting}
            isReassigning={isReassigning}
            chairs={chairs}
            onApprove={(finalText, targetStatus) =>
              approve({
                id: selectedEmail.id,
                data: {
                  approved_by: "chair",
                  final_text: finalText,
                  target_status: targetStatus,
                },
              })
            }
            onReroute={(reason) =>
              reroute({
                id: selectedEmail.id,
                data: { rerouted_by: "chair", reason, new_lane: "faq" },
              })
            }
            onReassignChair={(chairId, reason) =>
              reassignChairAsync({
                id: selectedEmail.id,
                data: { reassigned_by: "chair", new_chair_id: chairId, reason },
              })
            }
            onRetry={() => retry(selectedEmail.id)}
            allowAutoSend={allowAutoSend}
          />
        ) : (
          <div className="flex h-full items-center justify-center">
            <EmptyState
              icon={<Inbox className="h-6 w-6" />}
              title="Select an email to review"
              description="Choose an email from the queue to see details, policy citations, and the AI-generated draft."
            />
          </div>
        )}
      </section>
    </div>
  );
}
