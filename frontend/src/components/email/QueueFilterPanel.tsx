"use client";

import type { Chair } from "@/types";

import { DateRangeFilter } from "./DateRangeFilter";
import { EmailFilters } from "./EmailFilters";
import { SourceToggle } from "./SourceToggle";
import { ZendeskStatusBar } from "./ZendeskStatusBar";
import { Pagination } from "./Pagination";

type LaneFilter = "all" | "faq" | "human_review";
type StatusFilter = "all" | "PENDING" | "DRAFT_GENERATED" | "APPROVED";

interface QueueFilterPanelProps {
  // Core filters
  search: string;
  onSearchChange: (v: string) => void;
  laneFilter: LaneFilter;
  onLaneChange: (v: LaneFilter) => void;
  statusFilter: StatusFilter;
  onStatusChange: (v: string) => void;
  chairs: Chair[];
  chairFilter: string;
  onChairChange: (v: string) => void;
  // Source toggle (self-hides when < 2 sources)
  sources: string[];
  sourceFilter: string;
  onSourceChange: (v: string) => void;
  // Zendesk status bar
  showStatusBar: boolean;
  byZendeskStatus: Record<string, number>;
  zendeskStatusFilter: string | null;
  onZendeskStatusSelect: (v: string | null) => void;
  // Received-date window, as bare `YYYY-MM-DD` strings (null = unbounded on
  // that side). Emitted as a PAIR because the two bounds always change together
  // — Apply, a preset and Clear each set both at once, and splitting them into
  // two callbacks would fire two state updates and two query keys per action.
  receivedAfter: string | null;
  receivedBefore: string | null;
  onReceivedRangeChange: (after: string | null, before: string | null) => void;
  // Result count + pagination for the CURRENT filter set. `total` is the
  // backend's filtered total (accurate regardless of page size); `shownCount` is
  // how many rows the loaded page holds; `rangeStart` is the 1-based index of
  // the first row on this page (offset + 1).
  total: number;
  shownCount: number;
  rangeStart: number;
  page: number;
  pageCount: number;
  onPageChange: (page: number) => void;
}

/** "Showing 101–200 of 874 tickets" — or, when the whole set is on one page,
 * just "47 tickets". `rangeStart` is the 1-based index of the first row shown. */
function ticketCountLabel(
  total: number,
  shownCount: number,
  rangeStart: number
): string {
  if (total === 0) return "No tickets";
  const noun = total === 1 ? "ticket" : "tickets";
  if (shownCount >= total) return `${total} ${noun}`;
  const rangeEnd = rangeStart + shownCount - 1;
  return `Showing ${rangeStart}–${rangeEnd} of ${total} ${noun}`;
}

/**
 * The queue's full filter block — search / lane / status / chair, the source
 * toggle, and the Zendesk status bar — grouped for the queue's own filter
 * column (page-owned; see queue/page.tsx). Pure pass-through: all state and the
 * source-toggle side effect stay in the queue page.
 */
export function QueueFilterPanel({
  search,
  onSearchChange,
  laneFilter,
  onLaneChange,
  statusFilter,
  onStatusChange,
  chairs,
  chairFilter,
  onChairChange,
  sources,
  sourceFilter,
  onSourceChange,
  showStatusBar,
  byZendeskStatus,
  zendeskStatusFilter,
  onZendeskStatusSelect,
  receivedAfter,
  receivedBefore,
  onReceivedRangeChange,
  total,
  shownCount,
  rangeStart,
  page,
  pageCount,
  onPageChange,
}: QueueFilterPanelProps) {
  // No top border: this used to sit below the nav inside the sidebar, where the
  // rule separated the two. It now owns a column whose own right border
  // provides the separation, so a top rule would just be a stray line.
  return (
    // `flex-1` claims the column's leftover height so the footer below can be
    // pushed to the bottom; `gap-4` replaces the old `space-y-4` because that
    // utility sets margin-top on each child, which would collide with the
    // footer's `mt-auto`. Same 16px rhythm, no margin conflict.
    <div className="flex flex-1 flex-col gap-4 px-3 py-4">
      <EmailFilters
        search={search}
        onSearchChange={onSearchChange}
        laneFilter={laneFilter}
        onLaneChange={onLaneChange}
        statusFilter={statusFilter}
        onStatusChange={onStatusChange}
        chairs={chairs}
        chairFilter={chairFilter}
        onChairChange={onChairChange}
      />
      <SourceToggle
        sources={sources}
        value={sourceFilter}
        onChange={onSourceChange}
      />
      {showStatusBar && (
        <ZendeskStatusBar
          counts={byZendeskStatus}
          selected={zendeskStatusFilter}
          onSelect={onZendeskStatusSelect}
        />
      )}

      {/* Below the status bar, above the mt-auto footer. Spacing comes from the
          container's `gap-4` — deliberately NOT `space-y-*`, which sets
          margin-top on children and would collide with the footer's `mt-auto`. */}
      <DateRangeFilter
        receivedAfter={receivedAfter}
        receivedBefore={receivedBefore}
        onChange={onReceivedRangeChange}
      />

      {/* Result count + page controls for the active filter. `mt-auto` pins this
          to the BOTTOM of the column so it sits in a constant place regardless
          of how many filter rows render above it. When the filters overflow the
          column there is no free space to absorb, so `mt-auto` collapses to 0
          and this simply follows the content into the scroll — it never overlaps
          and is never stuck to the viewport. */}
      <div className="mt-auto space-y-2 pt-1">
        <p
          className="text-center text-xs"
          style={{ color: "var(--text-muted)" }}
          aria-live="polite"
        >
          {ticketCountLabel(total, shownCount, rangeStart)}
        </p>
        <Pagination
          page={page}
          pageCount={pageCount}
          onPageChange={onPageChange}
        />
      </div>
    </div>
  );
}
