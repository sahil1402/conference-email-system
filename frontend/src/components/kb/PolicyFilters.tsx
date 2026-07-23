"use client";

import { AlertTriangle, Search } from "lucide-react";

import { cn } from "@/lib/utils";

type VisibilityFilter = "all" | "public" | "internal";
type StatusFilter = "active" | "inactive" | "all";

interface PolicyFiltersProps {
  search: string;
  onSearchChange: (v: string) => void;
  visibility: VisibilityFilter;
  onVisibilityChange: (v: VisibilityFilter) => void;
  status: StatusFilter;
  onStatusChange: (v: StatusFilter) => void;
  /** "Conflicts only": show only active policies with a live conflict (2e). */
  conflictsOnly: boolean;
  onConflictsOnlyChange: (v: boolean) => void;
}

const VISIBILITY_OPTIONS: { value: VisibilityFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "public", label: "Public" },
  { value: "internal", label: "Internal" },
];

const STATUS_OPTIONS: { value: StatusFilter; label: string }[] = [
  { value: "active", label: "Active" },
  { value: "inactive", label: "Inactive" },
  { value: "all", label: "All" },
];

/** Search + visibility toggle + status toggle for the Knowledge Base policies list. */
export function PolicyFilters({
  search,
  onSearchChange,
  visibility,
  onVisibilityChange,
  status,
  onStatusChange,
  conflictsOnly,
  onConflictsOnlyChange,
}: PolicyFiltersProps) {
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
          placeholder="Search policy key, title, or content…"
          className="w-full rounded-lg border py-2 pl-9 pr-3 text-sm outline-none transition-colors focus:border-[var(--accent)]"
          style={{
            backgroundColor: "var(--surface)",
            borderColor: "var(--border)",
            color: "var(--text-primary)",
          }}
        />
      </div>

      {/* Visibility toggle */}
      <div
        className="flex gap-1 rounded-lg p-1"
        style={{ backgroundColor: "var(--surface)" }}
      >
        {VISIBILITY_OPTIONS.map(({ value, label }) => {
          const active = visibility === value;
          return (
            <button
              key={value}
              type="button"
              onClick={() => onVisibilityChange(value)}
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

      {/* Status toggle — disabled while "Conflicts only" is on (it forces active). */}
      <div
        className="flex gap-1 rounded-lg p-1"
        style={{ backgroundColor: "var(--surface)", opacity: conflictsOnly ? 0.5 : 1 }}
      >
        {STATUS_OPTIONS.map(({ value, label }) => {
          const active = status === value;
          return (
            <button
              key={value}
              type="button"
              onClick={() => onStatusChange(value)}
              disabled={conflictsOnly}
              className={cn(
                "flex-1 rounded-md px-2 py-1.5 text-xs font-medium transition-colors",
                conflictsOnly && "cursor-not-allowed"
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

      {/* Conflicts-only toggle (2e): active policies with a live conflict. */}
      <button
        type="button"
        onClick={() => onConflictsOnlyChange(!conflictsOnly)}
        aria-pressed={conflictsOnly}
        className="flex items-center justify-center gap-1.5 rounded-lg px-2 py-1.5 text-xs font-medium transition-colors"
        style={
          conflictsOnly
            ? { backgroundColor: "var(--danger-subtle)", color: "var(--danger)" }
            : { backgroundColor: "var(--surface)", color: "var(--text-secondary)" }
        }
      >
        <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
        {conflictsOnly ? "Showing conflicts only" : "Conflicts only"}
      </button>
    </div>
  );
}
