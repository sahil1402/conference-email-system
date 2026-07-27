"use client";

import type { Chair } from "@/types";

import { EmailFilters } from "./EmailFilters";
import { SourceToggle } from "./SourceToggle";
import { ZendeskStatusBar } from "./ZendeskStatusBar";

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
  // Result count for the CURRENT filter set. `total` is the backend's filtered
  // total (accurate regardless of page size); `shownCount` is how many rows the
  // loaded page holds.
  total: number;
  shownCount: number;
}

/** "Showing 1–100 of 874 tickets" — or, when the whole set is on one page,
 * just "47 tickets". Range reflects the loaded page (offset is 0 until page
 * controls land in a later step). */
function ticketCountLabel(total: number, shownCount: number): string {
  if (total === 0) return "No tickets";
  const noun = total === 1 ? "ticket" : "tickets";
  if (shownCount >= total) return `${total} ${noun}`;
  return `Showing 1–${shownCount} of ${total} ${noun}`;
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
  total,
  shownCount,
}: QueueFilterPanelProps) {
  // No top border: this used to sit below the nav inside the sidebar, where the
  // rule separated the two. It now owns a column whose own right border
  // provides the separation, so a top rule would just be a stray line.
  return (
    <div className="space-y-4 px-3 py-4">
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

      {/* Result count for the active filter — sits below the Zendesk status
          section. Page controls will build on this in a later step. */}
      <p
        className="pt-1 text-xs"
        style={{ color: "var(--text-muted)" }}
        aria-live="polite"
      >
        {ticketCountLabel(total, shownCount)}
      </p>
    </div>
  );
}
