"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { AlertCircle, Inbox, PanelLeft, SearchX, X } from "lucide-react";

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

import { useEmailQueue } from "@/hooks/useEmailQueue";
import { useResizableWidth } from "@/hooks/useResizableWidth";
import { usePersistedState } from "@/hooks/usePersistedState";
import { useEmailQueueStream } from "@/hooks/useEmailQueueStream";
import { useQueueFacets } from "@/hooks/useQueueFacets";
import type { EmailQueueParams, QueueFacetsParams } from "@/lib/api";
import {
  useApproveEmail,
  useRerouteEmail,
  useReassignChair,
  useRetryEmail,
  useSendEmail,
} from "@/hooks/useEmailActions";
import { useChairs } from "@/hooks/useChairs";
import { useAppConfig } from "@/hooks/useAppConfig";
import { EmailListItem } from "./EmailListItem";
import { EmailDetail } from "./EmailDetail";
import { QueueFilterPanel } from "./QueueFilterPanel";
import {
  Badge,
  EmptyState,
  ErrorBanner,
  LiveStatusDot,
  LoadingSpinner,
} from "@/components/ui";
import { cn } from "@/lib/utils";
import type { Email } from "@/types";

type LaneFilter = "all" | "faq" | "human_review";

/**
 * Fixed chrome widths (px) used to work out how much room the list and detail
 * panes actually have. Kept next to the markup that produces them so the two
 * can't drift silently.
 */
/** Mirrors --rail-width in globals.css — keep the two in step. */
const RAIL_WIDTH = 52;
/** Filter column expanded: w-64 plus its 1px right border. */
const FILTER_COL_EXPANDED = 256 + 1;
/** Filter column collapsed: w-[52px] plus its 1px right border. */
const FILTER_COL_COLLAPSED = 52 + 1;
/** The drag handle between the list and the detail pane: w-1.5. */
const DRAG_HANDLE_WIDTH = 6;

export interface EmailWorkspaceProps {
  /**
   * How the detail selection is resolved:
   * - "id" (queue): the selected row id drives the detail pane, resolved from
   *   the loaded queue list. Instant, no fetch — no loading/error state.
   * - "email" (ticket route): the selected email is supplied externally (e.g.
   *   fetched by ticket id), so it need not be on the current queue page; the
   *   row highlight is matched by that email's id.
   */
  selectionMode?: "id" | "email";
  /** "id" mode: the selected row id (drives the detail pane + row highlight). */
  selectedEmailId?: number | null;
  /** "email" mode: the externally-resolved selected email. */
  selectedEmail?: Email | null;
  /**
   * Open a ticket by its Zendesk ticket id. Fired on a row click, on the
   * post-send advance to the neighbouring ticket, and from the failed-send
   * notice's links. Both routes wire this to router.push(`/tickets/${id}`), so
   * selection is URL-driven (every viewed email has a shareable URL); null
   * (e.g. no neighbour to advance to) is a no-op. Replaces the former in-memory
   * DB-id selection setter.
   */
  onOpenTicket: (ticketId: number | null) => void;
  /** "email" mode: the external fetch is in flight (queue leaves this false). */
  detailLoading?: boolean;
  /**
   * "email" mode: content to show in the detail pane when the external fetch
   * did not yield an email — e.g. a not-found empty state or a generic error
   * banner. Rendered centered in the pane (the caller owns the exact node so it
   * can distinguish 404 from other failures). Falsy → normal detail rendering.
   * The queue leaves this unset.
   */
  detailError?: ReactNode;
  /** Extra content rendered beneath EmailDetail in the right pane (e.g. the
   *  ticket route's activity trail). Omitted on the queue. */
  detailFooter?: ReactNode;
}

/**
 * The full 3-column email review workspace — collapsible filter sidebar,
 * drag-resizable list, and the detail pane with all chair-action wiring
 * (approve → send chain, reroute, reassign, retry, failed-send notices).
 *
 * Shared verbatim by /queue and /tickets/[ticketId] so filters/resize/list/
 * detail live in ONE place. The ONLY thing the two routes vary is how the
 * selected email is resolved — see {@link EmailWorkspaceProps.selectionMode}.
 */
export function EmailWorkspace({
  selectionMode = "id",
  selectedEmailId = null,
  selectedEmail: selectedEmailProp = null,
  onOpenTicket,
  detailLoading = false,
  detailError = null,
  detailFooter,
}: EmailWorkspaceProps) {
  const { status: streamStatus } = useEmailQueueStream();
  const { mutate: approve, isPending: isApproving } = useApproveEmail();
  const sendMutation = useSendEmail();
  const { mutate: send } = sendMutation;
  const { mutate: reroute, isPending: isRerouting } = useRerouteEmail();
  const { mutateAsync: reassignChairAsync, isPending: isReassigning } =
    useReassignChair();
  const { mutate: retry } = useRetryEmail();
  const { allowAutoSend } = useAppConfig();
  const { chairs, byId: chairsById } = useChairs();

  // Filter column collapse state (N4a). Persisted so the chair's choice sticks
  // across reloads, like the list width and the submit-as preferences.
  const [filterColumnCollapsed, setFilterColumnCollapsed] =
    usePersistedState<boolean>("confmail.filterColumnCollapsed", false);
  const toggleFilterColumn = () => setFilterColumnCollapsed((v) => !v);
  const filterToggleLabel = filterColumnCollapsed
    ? "Show filters"
    : "Hide filters";

  // Chrome that is never available to the list or the detail pane. Tracks the
  // filter column's collapse state so collapsing actually hands the freed space
  // back to the detail pane.
  const reservedWidth =
    RAIL_WIDTH +
    (filterColumnCollapsed ? FILTER_COL_COLLAPSED : FILTER_COL_EXPANDED) +
    DRAG_HANDLE_WIDTH;

  // Queue-list column: draggable + persisted width. The viewport-aware bounds
  // stop a width saved on a wide monitor from squeezing the detail pane when
  // the same width is restored on a smaller screen.
  const { width: listWidth, isDragging, handleProps } = useResizableWidth(
    "confmail.queueListWidth",
    320,
    240,
    640,
    {
      reservedWidth,
      // Floor for the detail pane: below this, reading/editing a draft gets
      // cramped. 440 is also the largest value that still lets the list keep
      // its 240px minimum on a 1024px viewport (315 + 240 + 440 = 995).
      minRemainingWidth: 440,
    }
  );

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

  // The selected email + the row to highlight. In "id" mode both come from the
  // loaded list; in "email" mode the email is supplied externally and the
  // highlight is matched by its id (it may or may not be on the current page).
  const selectedEmail =
    selectionMode === "email"
      ? selectedEmailProp
      : selectedEmailId == null
        ? null
        : emails.find((e) => e.id === selectedEmailId) ?? null;
  const highlightId =
    selectionMode === "email" ? selectedEmailProp?.id ?? null : selectedEmailId;

  // The email to advance to after acting on `currentId`: the next row down,
  // else the previous, else none. Returns the EMAIL (not just an id) so the
  // caller can navigate by its zendesk_ticket_id. Computed from the CURRENT list
  // snapshot (before the post-send refetch drops the acted-on ticket).
  const neighborEmail = (currentId: number): Email | null => {
    const idx = emails.findIndex((e) => e.id === currentId);
    if (idx === -1) return null;
    return emails[idx + 1] ?? emails[idx - 1] ?? null;
  };

  // Failed-send tracking. Advance is now navigate-on-success (see onApprove), so
  // a send that fails keeps us on the ticket and surfaces the selection-scoped
  // banner below — the queue-level notice here only lights up for a failure
  // recorded against a ticket other than the one in view (kept for the reopen
  // affordance; fuller cross-navigation surfacing is a follow-up). The ticket is
  // marked SEND_FAILED server-side and, as the queue applies no default status
  // filter, stays visible in the list.
  const [failedSends, setFailedSends] = useState<
    { id: number; ticket: number | null }[]
  >([]);
  const noteSendFailed = (email: {
    id: number;
    zendesk_ticket_id?: number | null;
  }) =>
    setFailedSends((prev) => [
      ...prev.filter((f) => f.id !== email.id),
      { id: email.id, ticket: email.zendesk_ticket_id ?? null },
    ]);
  const clearSendFailed = (id: number) =>
    setFailedSends((prev) => prev.filter((f) => f.id !== id));

  // The queue-level notice lists failed sends for tickets the chair is NOT
  // currently viewing — a selected-and-failed ticket shows the richer
  // selection-scoped banner in the detail pane instead, so don't double up.
  const unseenFailedSends = failedSends.filter((f) => f.id !== highlightId);

  // Approve-then-send partial failure, scoped to the selected email: the approve
  // succeeded (its own state/error is out of scope here) but the follow-up send
  // to Zendesk failed. `sendMutation.variables.id` identifies which email the
  // last send attempt was for, so the banner only shows on that one.
  const sendFailedForSelected =
    sendMutation.isError &&
    selectedEmail != null &&
    sendMutation.variables?.id === selectedEmail.id;
  const sendErrorMessage = sendFailedForSelected
    ? `This email is approved locally, but sending it to Zendesk failed${
        sendMutation.error?.detail ? `: ${sendMutation.error.detail}` : "."
      } It was NOT sent — the approval stands; retry the send.`
    : null;

  // Full-height minus the top bar so the split-pane fills the viewport without
  // overflowing: the mobile bar below md (pt-14 = 3.5rem on <main>), the desktop
  // bar at md+ (--topbar-height).
  return (
    <div className="flex h-[calc(100vh-3.5rem)] overflow-hidden md:h-[calc(100vh-var(--topbar-height))]">
      {/* FILTER COLUMN — page-owned. The filters used to portal into a slot in
          the sidebar; they now render here, directly in the workspace's own
          layout, with the same held state. */}
      <div
        data-collapsed={filterColumnCollapsed}
        className={cn(
          "shrink-0 overflow-y-auto overflow-x-hidden transition-[width] duration-200",
          // Collapsed: 36px button + px-2 either side = 52px, the same rhythm
          // as the nav rail. overflow-x-hidden stops the panel (which mounts at
          // full width) from flashing a scrollbar while the width animates.
          filterColumnCollapsed ? "w-[52px]" : "w-64"
        )}
        style={{ borderRight: "1px solid var(--border)" }}
      >
        {/* Collapse toggle — rendered in BOTH states; it's the only way back
            from collapsed. Its own provider: the rail's lives inside Sidebar,
            a sibling of <main>, so it isn't an ancestor of this button. */}
        <TooltipProvider>
          {/* Constant px-2 in both states: the toggle stays flush-left at a
              fixed 8px inset (no jump when the column width changes), matching
              the nav rail's icon inset so the two line up vertically. */}
          <div className="px-2 pt-4">
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  onClick={toggleFilterColumn}
                  aria-label={filterToggleLabel}
                  aria-expanded={!filterColumnCollapsed}
                  className={cn(
                    "flex h-9 w-9 items-center justify-center rounded-lg transition-colors duration-150",
                    "hover:bg-[var(--surface-raised)]",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
                    "focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--background)]"
                  )}
                  style={{ color: "var(--text-secondary)" }}
                >
                  <PanelLeft className="h-4 w-4" aria-hidden />
                </button>
              </TooltipTrigger>
              <TooltipContent side="right">{filterToggleLabel}</TooltipContent>
            </Tooltip>
          </div>
        </TooltipProvider>

        {!filterColumnCollapsed && (
          <QueueFilterPanel
            search={search}
            onSearchChange={setSearch}
            laneFilter={laneFilter}
            onLaneChange={setLaneFilter}
            statusFilter={
              statusFilter as "all" | "PENDING" | "DRAFT_GENERATED" | "APPROVED"
            }
            onStatusChange={setStatusFilter}
            chairs={chairs}
            chairFilter={chairFilter}
            onChairChange={setChairFilter}
            sources={sources}
            sourceFilter={sourceFilter}
            onSourceChange={(v) => {
              setSourceFilter(v);
              // A zendesk_status filter is meaningless once we scope to
              // toy_dataset — clear it so the queue isn't silently emptied.
              if (v === "toy_dataset") setZendeskStatusFilter(null);
            }}
            showStatusBar={showStatusBar}
            byZendeskStatus={byZendeskStatus}
            zendeskStatusFilter={zendeskStatusFilter}
            onZendeskStatusSelect={setZendeskStatusFilter}
          />
        )}
      </div>

      {/* LEFT PANE */}
      <aside
        className="flex shrink-0 flex-col"
        style={{ width: listWidth, borderRight: "1px solid var(--border)" }}
      >
        <div className="p-4" style={{ borderBottom: "1px solid var(--border-subtle)" }}>
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
        </div>

        {unseenFailedSends.length > 0 && (
          <div
            className="px-4 py-3"
            style={{ borderBottom: "1px solid var(--border-subtle)" }}
          >
            <div
              className="flex items-start gap-3 rounded-xl border px-4 py-3 text-sm"
              style={{
                backgroundColor: "var(--danger-subtle)",
                borderColor: "var(--danger)",
                color: "var(--text-primary)",
              }}
              role="alert"
            >
              <AlertCircle
                className="mt-0.5 h-5 w-5 shrink-0"
                style={{ color: "var(--danger)" }}
              />
              <div className="min-w-0 flex-1">
                <p>
                  {unseenFailedSends.length === 1
                    ? "A reply failed to send — it stays in the queue."
                    : `${unseenFailedSends.length} replies failed to send — they stay in the queue.`}{" "}
                  Open to retry:
                </p>
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {unseenFailedSends.map((f) => (
                    <button
                      key={f.id}
                      type="button"
                      onClick={() => onOpenTicket(f.ticket)}
                      className="rounded-md px-2 py-0.5 text-xs font-medium transition-colors hover:opacity-80"
                      style={{
                        color: "var(--danger)",
                        backgroundColor: "rgba(239, 68, 68, 0.12)",
                      }}
                    >
                      {f.ticket != null ? `#${f.ticket}` : `Email ${f.id}`}
                    </button>
                  ))}
                </div>
              </div>
              <button
                type="button"
                onClick={() => setFailedSends([])}
                aria-label="Dismiss send-failure notice"
                className="shrink-0 rounded-md p-1 transition-colors hover:bg-[var(--surface)]"
                style={{ color: "var(--text-muted)" }}
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>
        )}

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
                    isSelected={email.id === highlightId}
                    onClick={() => onOpenTicket(email.zendesk_ticket_id ?? null)}
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

      {/* Drag handle — resize the queue-list column (width persisted). */}
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize the email list"
        className="group relative flex w-1.5 shrink-0 cursor-col-resize items-stretch justify-center"
        {...handleProps}
      >
        <div
          className={cn(
            "w-px transition-colors group-hover:bg-[var(--accent)]",
            isDragging ? "bg-[var(--accent)]" : "bg-[var(--border)]"
          )}
        />
      </div>

      {/* RIGHT PANE */}
      <section className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {detailLoading ? (
          <div className="flex h-full items-center justify-center">
            <LoadingSpinner size="lg" />
          </div>
        ) : detailError ? (
          <div className="flex h-full items-center justify-center p-6">
            {detailError}
          </div>
        ) : selectedEmail ? (
          <>
            <div className="min-h-0 flex-1">
              <EmailDetail
                key={selectedEmail.id}
                email={selectedEmail}
                isApproving={isApproving}
                isRerouting={isRerouting}
                isReassigning={isReassigning}
                chairs={chairs}
                onApprove={(finalText, targetStatus, isPublic) =>
                  // Approve first; on success, release the draft to the ticket
                  // with the chosen visibility + status.
                  approve(
                    {
                      id: selectedEmail.id,
                      data: {
                        approved_by: "chair",
                        final_text: finalText,
                        target_status: targetStatus,
                      },
                    },
                    {
                      onSuccess: () => {
                        // Release to Zendesk, then advance by NAVIGATING to the
                        // neighbouring ticket once the send lands. On failure we
                        // stay on this ticket so the scoped "approved locally —
                        // retry the send" banner is actionable. Capture the ticket
                        // + neighbour up front (the send's refetch drops this row
                        // from the list).
                        const current = selectedEmail;
                        const next = neighborEmail(current.id);
                        send(
                          {
                            id: current.id,
                            data: {
                              public: isPublic,
                              target_status: targetStatus,
                            },
                          },
                          {
                            onSuccess: () => {
                              clearSendFailed(current.id);
                              onOpenTicket(next?.zendesk_ticket_id ?? null);
                            },
                            onError: () => noteSendFailed(current),
                          }
                        );
                      },
                    }
                  )
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
                sendError={sendErrorMessage}
                onRetrySend={() => {
                  // Retry ONLY the send with the same visibility + status — never
                  // re-approve. `variables` holds the failed attempt's payload. A
                  // deliberate retry stays on the ticket until it lands (unlike
                  // the optimistic first-try flow), so the chair sees the
                  // outcome; on success, clear the notice and advance.
                  if (sendMutation.variables) {
                    const current = selectedEmail;
                    const next = neighborEmail(current.id);
                    send(sendMutation.variables, {
                      onSuccess: () => {
                        clearSendFailed(current.id);
                        onOpenTicket(next?.zendesk_ticket_id ?? null);
                      },
                      onError: () => noteSendFailed(current),
                    });
                  }
                }}
              />
            </div>
            {detailFooter}
          </>
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
