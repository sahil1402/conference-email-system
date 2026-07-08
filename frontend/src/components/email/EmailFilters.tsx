"use client";

import { Search } from "lucide-react";

import { cn } from "@/lib/utils";
import type { Chair } from "@/types";

type LaneFilter = "all" | "faq" | "human_review";

interface EmailFiltersProps {
  search: string;
  onSearchChange: (v: string) => void;
  laneFilter: LaneFilter;
  onLaneChange: (v: LaneFilter) => void;
  statusFilter: "all" | "PENDING" | "DRAFT_GENERATED" | "APPROVED";
  onStatusChange: (v: string) => void;
  /** Chair roster for the assigned-chair filter (Phase 6A). */
  chairs: Chair[];
  /** "all" | "unassigned" | chair id (as a string). */
  chairFilter: string;
  onChairChange: (v: string) => void;
}

const LANE_OPTIONS: { value: LaneFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "faq", label: "FAQ" },
  { value: "human_review", label: "Review" },
];

const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: "all", label: "All statuses" },
  { value: "PENDING", label: "Pending" },
  { value: "DRAFT_GENERATED", label: "Draft Generated" },
  { value: "APPROVED", label: "Approved" },
];

/** Search + lane toggle + status dropdown for the queue list. */
export function EmailFilters({
  search,
  onSearchChange,
  laneFilter,
  onLaneChange,
  statusFilter,
  onStatusChange,
  chairs,
  chairFilter,
  onChairChange,
}: EmailFiltersProps) {
  return (
    <div className="flex flex-col gap-3">
      {/* Search */}
      <div className="relative">
        <Search
          className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
          style={{ color: "var(--text-muted)" }}
        />
        <input
          type="text"
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder="Search subject or sender…"
          className="w-full rounded-lg border py-2 pl-9 pr-3 text-sm outline-none transition-colors focus:border-[var(--accent)]"
          style={{
            backgroundColor: "var(--surface)",
            borderColor: "var(--border)",
            color: "var(--text-primary)",
          }}
        />
      </div>

      {/* Lane toggle */}
      <div
        className="flex gap-1 rounded-lg p-1"
        style={{ backgroundColor: "var(--surface)" }}
      >
        {LANE_OPTIONS.map(({ value, label }) => {
          const active = laneFilter === value;
          return (
            <button
              key={value}
              type="button"
              onClick={() => onLaneChange(value)}
              className={cn(
                "flex-1 rounded-md px-2 py-1.5 text-xs font-medium transition-colors"
              )}
              style={
                active
                  ? {
                      backgroundColor: "var(--accent-subtle)",
                      color: "var(--accent)",
                    }
                  : { color: "var(--text-secondary)" }
              }
            >
              {label}
            </button>
          );
        })}
      </div>

      {/* Status dropdown */}
      <select
        value={statusFilter}
        onChange={(e) => onStatusChange(e.target.value)}
        className="w-full rounded-lg border px-3 py-2 text-sm outline-none transition-colors focus:border-[var(--accent)]"
        style={{
          backgroundColor: "var(--surface)",
          borderColor: "var(--border)",
          color: "var(--text-primary)",
        }}
      >
        {STATUS_OPTIONS.map(({ value, label }) => (
          <option
            key={value}
            value={value}
            style={{ backgroundColor: "var(--surface)" }}
          >
            {label}
          </option>
        ))}
      </select>

      {/* Assigned-chair dropdown (Phase 6A) */}
      <select
        value={chairFilter}
        onChange={(e) => onChairChange(e.target.value)}
        aria-label="Filter by assigned chair"
        className="w-full rounded-lg border px-3 py-2 text-sm outline-none transition-colors focus:border-[var(--accent)]"
        style={{
          backgroundColor: "var(--surface)",
          borderColor: "var(--border)",
          color: "var(--text-primary)",
        }}
      >
        <option value="all" style={{ backgroundColor: "var(--surface)" }}>
          All chairs
        </option>
        <option value="unassigned" style={{ backgroundColor: "var(--surface)" }}>
          Unassigned
        </option>
        {chairs.map((chair) => (
          <option
            key={chair.id}
            value={String(chair.id)}
            style={{ backgroundColor: "var(--surface)" }}
          >
            {chair.name}
          </option>
        ))}
      </select>
    </div>
  );
}
