"use client";

import * as React from "react";
import { CalendarDays } from "lucide-react";
import {
  format,
  isSameDay,
  isSameYear,
  isValid,
  parse,
  subDays,
  subMonths,
} from "date-fns";
import type { DateRange } from "react-day-picker";

import { Calendar } from "@/components/ui/calendar";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

/**
 * Queue filter for the `received_at` window.
 *
 * Presentational only, matching SourceToggle / ZendeskStatusBar: flat props in,
 * one `onChange` out, no filter state of its own. (The popover's open/closed
 * flag is transient UI state, not filter state.) Wiring into EmailWorkspace's
 * usePersistedState / filterKey / contextParams is a separate step.
 *
 * ⚠️ OUTPUT CONTRACT — bare `YYYY-MM-DD`, never a timestamp. The backend
 * documents that a bare date covers the WHOLE day (`received_after` -> 00:00:00,
 * `received_before` -> 23:59:59.999999) and owns that expansion. This component
 * must therefore do NO end-of-day arithmetic; appending a time here would opt
 * out of the server-side widening and silently drop most of the final day.
 *
 * ⚠️ TIMEZONE — every conversion goes through date-fns `format` / `parse`, which
 * work in LOCAL time. The obvious shortcuts are both broken:
 *   • `date.toISOString().slice(0, 10)` serializes in UTC, so a user east of UTC
 *     picking the 20th sends the 19th.
 *   • `new Date("2026-01-20")` parses as UTC midnight, so a user west of UTC
 *     sees the calendar highlight the 19th — confirmed on this machine (UTC-7),
 *     where it renders "Mon Jan 19".
 * A date the chair picked must round-trip to the same calendar day they saw.
 */

export interface DateRangeFilterProps {
  /** Lower bound, inclusive, as `YYYY-MM-DD`. Null when unset. */
  receivedAfter: string | null;
  /** Upper bound, inclusive, as `YYYY-MM-DD`. Null when unset. */
  receivedBefore: string | null;
  /** Emits the new bounds. Both null clears the filter. */
  onChange: (after: string | null, before: string | null) => void;
}

const PARAM_FORMAT = "yyyy-MM-dd";

/** Date -> the wire format. Local, never UTC (see the timezone note above). */
function toParam(date: Date): string {
  return format(date, PARAM_FORMAT);
}

/** Wire format -> Date. Returns null for absent/unparseable input, so a corrupt
 *  persisted value degrades to "no bound" instead of rendering Invalid Date. */
function fromParam(value: string | null): Date | null {
  if (!value) return null;
  const parsed = parse(value, PARAM_FORMAT, new Date());
  return isValid(parsed) ? parsed : null;
}

/**
 * Preset windows. All are ROLLING and inclusive of today rather than aligned to
 * calendar boundaries ("last week" = the previous 7 days, not last Mon–Sun),
 * which is the more useful reading for a support queue where the question is
 * "what came in recently". Flagged for confirmation — the alternative reading is
 * defensible and this is a product decision, not a technical one.
 */
const PRESETS: { label: string; resolve: (today: Date) => [Date, Date] }[] = [
  { label: "Today", resolve: (t) => [t, t] },
  { label: "Yesterday", resolve: (t) => [subDays(t, 1), subDays(t, 1)] },
  { label: "Last week", resolve: (t) => [subDays(t, 6), t] },
  { label: "Last month", resolve: (t) => [subMonths(t, 1), t] },
  { label: "Last quarter", resolve: (t) => [subMonths(t, 3), t] },
];

/** "Jul 21", "Jul 21 – Jul 31", "Dec 28, 2025 – Jan 4, 2026", "From Jul 21". */
function formatRangeLabel(after: Date | null, before: Date | null): string {
  if (!after && !before) return "All dates";

  // Years are shown only when the range crosses a year boundary or leaves the
  // current one, so the common case stays short enough for the sidebar.
  const now = new Date();
  const needsYear = [after, before].some((d) => d && !isSameYear(d, now));
  const fmt = (d: Date) => format(d, needsYear ? "MMM d, yyyy" : "MMM d");

  if (after && before) {
    return isSameDay(after, before) ? fmt(after) : `${fmt(after)} – ${fmt(before)}`;
  }
  return after ? `From ${fmt(after)}` : `Until ${fmt(before as Date)}`;
}

export function DateRangeFilter({
  receivedAfter,
  receivedBefore,
  onChange,
}: DateRangeFilterProps) {
  const [open, setOpen] = React.useState(false);

  const after = fromParam(receivedAfter);
  const before = fromParam(receivedBefore);
  const isActive = after !== null || before !== null;

  // react-day-picker wants `to` omitted rather than null for an open range.
  const selected: DateRange | undefined = after
    ? { from: after, to: before ?? undefined }
    : before
      ? { from: before, to: before }
      : undefined;

  const applyPreset = (resolve: (today: Date) => [Date, Date]) => {
    const [from, to] = resolve(new Date());
    onChange(toParam(from), toParam(to));
    setOpen(false);
  };

  const handleSelect = (range: DateRange | undefined) => {
    if (!range?.from) {
      onChange(null, null);
      return;
    }
    // Committed on EVERY click, not only once both ends exist: an open-ended
    // "from the 21st onward" is a legitimate filter the backend supports, and
    // committing immediately gives a live preview of the narrowing queue. The
    // popover stays open until the range is complete so the second click has
    // something to land on.
    onChange(toParam(range.from), range.to ? toParam(range.to) : null);
    if (range.to) setOpen(false);
  };

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between">
        <span
          className="text-xs font-medium uppercase tracking-wide"
          style={{ color: "var(--text-muted)" }}
        >
          Received
        </span>
        {isActive && (
          <button
            type="button"
            onClick={() => onChange(null, null)}
            className="text-xs transition-colors hover:underline"
            style={{ color: "var(--text-secondary)" }}
          >
            Clear
          </button>
        )}
      </div>

      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            aria-label={`Filter by received date. Current: ${formatRangeLabel(after, before)}`}
            className={cn(
              "flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm",
              "outline-none transition-colors focus:border-[var(--accent)]"
            )}
            style={{
              backgroundColor: "var(--surface)",
              borderColor: isActive ? "var(--accent)" : "var(--border)",
              color: isActive ? "var(--text-primary)" : "var(--text-secondary)",
            }}
          >
            <CalendarDays
              className="h-4 w-4 shrink-0"
              style={{ color: isActive ? "var(--accent)" : "var(--text-muted)" }}
            />
            <span className="truncate">{formatRangeLabel(after, before)}</span>
          </button>
        </PopoverTrigger>

        {/* p-0 overrides PopoverContent's default padding so the preset rail can
            run edge-to-edge against the divider. */}
        <PopoverContent align="start" className="p-0">
          <div className="flex flex-col sm:flex-row">
            <div
              className="flex shrink-0 flex-col gap-0.5 p-2 sm:border-r"
              style={{ borderColor: "var(--border)" }}
            >
              {PRESETS.map(({ label, resolve }) => (
                <button
                  key={label}
                  type="button"
                  onClick={() => applyPreset(resolve)}
                  className="rounded-md px-3 py-1.5 text-left text-sm transition-colors hover:bg-[var(--surface-raised)]"
                  style={{ color: "var(--text-secondary)" }}
                >
                  {label}
                </button>
              ))}
              {isActive && (
                <>
                  <div
                    className="my-1 h-px"
                    style={{ backgroundColor: "var(--border)" }}
                  />
                  <button
                    type="button"
                    onClick={() => {
                      onChange(null, null);
                      setOpen(false);
                    }}
                    className="rounded-md px-3 py-1.5 text-left text-sm transition-colors hover:bg-[var(--surface-raised)]"
                    style={{ color: "var(--text-muted)" }}
                  >
                    Reset
                  </button>
                </>
              )}
            </div>

            <Calendar
              mode="range"
              selected={selected}
              onSelect={handleSelect}
              defaultMonth={after ?? before ?? undefined}
              showOutsideDays
            />
          </div>
        </PopoverContent>
      </Popover>
    </div>
  );
}
