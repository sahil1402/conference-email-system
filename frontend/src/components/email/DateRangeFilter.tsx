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
 * one `onChange` out, no committed filter state of its own. Wiring into
 * EmailWorkspace's usePersistedState / filterKey / contextParams is separate.
 *
 * DRAFT + APPLY. Everything inside the popover edits a DRAFT; nothing reaches
 * `onChange` until Apply. The first version committed on every calendar click,
 * which made building a range unusable — picking a start date immediately fired
 * a filter change and closed the popover, so there was no room to navigate
 * months or deliberately choose an end date. One rule now governs the whole
 * popover: **nothing commits until Apply**. That rule is why the presets set the
 * draft too rather than committing on click (see PRESETS below) — a popover
 * where some clicks commit and others don't is exactly the confusion this
 * replaced.
 *
 * Two Clear-ish affordances, deliberately different, split by where they live:
 *   • header "Clear" (OUTSIDE the popover) — a committed action, fires
 *     onChange(null, null) immediately. This is the ZendeskStatusBar pattern:
 *     a one-click way to drop the filter without opening anything.
 *   • footer "Reset" (INSIDE the popover) — a draft action. Clears the draft and
 *     keeps the popover open so a fresh range can be picked in the same session.
 *     It does NOT commit; Apply still owns that.
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
 * "what came in recently".
 *
 * Presets populate the DRAFT and leave the popover open — they do not commit.
 * The alternative (commit + close, since a preset is one click and already a
 * complete range) is defensible and is a one-line change: call `commit(...)`
 * instead of `setDraft(...)` in `applyPreset`. It is not the default because a
 * popover in which some controls commit and others don't is the exact confusion
 * the draft flow replaced, and because landing a preset in the draft lets a
 * chair take "Last month" and then nudge one end before applying.
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

  /** The COMMITTED range, as react-day-picker wants it (`to` omitted, not null). */
  const committed = React.useMemo<DateRange | undefined>(() => {
    if (after) return { from: after, to: before ?? undefined };
    if (before) return { from: before, to: before };
    return undefined;
    // Derived from the string props; Date objects are rebuilt each render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [receivedAfter, receivedBefore]);

  const [draft, setDraft] = React.useState<DateRange | undefined>(committed);
  // The visible month is controlled so a preset can jump the view to its start
  // (e.g. "Last quarter" lands three months back) and so month navigation is
  // preserved while building a range.
  const [month, setMonth] = React.useState<Date>(committed?.from ?? new Date());

  /**
   * Re-seed the draft from the COMMITTED value on every open, which is what
   * makes cancelling clean: clicking away or pressing Escape closes without
   * committing, and the abandoned draft can never resurface on the next open.
   */
  const handleOpenChange = (next: boolean) => {
    if (next) {
      setDraft(committed);
      setMonth(committed?.from ?? new Date());
    }
    setOpen(next);
  };

  const commit = (range: DateRange | undefined) => {
    if (!range?.from) {
      onChange(null, null);
    } else {
      // A single picked day is a valid one-day range (from == to), which the
      // backend's inclusive bounds handle natively.
      onChange(toParam(range.from), toParam(range.to ?? range.from));
    }
    setOpen(false);
  };

  const applyPreset = (resolve: (today: Date) => [Date, Date]) => {
    const [from, to] = resolve(new Date());
    setDraft({ from, to });
    setMonth(from);
  };

  // Nothing to apply only when the draft is empty AND no filter is committed —
  // in every other case Apply is meaningful, including "no edits were made"
  // (re-commits the current range) and "Reset was pressed" (commits a clear).
  // Inverted ranges need no guard here: react-day-picker's range mode always
  // returns from <= to, so an invalid range is unrepresentable.
  const applyDisabled = !draft?.from && !isActive;

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

      <Popover open={open} onOpenChange={handleOpenChange}>
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

        {/* p-0 overrides PopoverContent's default padding so the preset rail and
            the footer can run edge-to-edge against their dividers. */}
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
            </div>

            <div className="flex flex-col">
              <Calendar
                mode="range"
                selected={draft}
                onSelect={setDraft}
                month={month}
                onMonthChange={setMonth}
                showOutsideDays
              />

              <div
                className="flex items-center justify-between gap-2 border-t p-2"
                style={{ borderColor: "var(--border)" }}
              >
                <span className="truncate text-xs" style={{ color: "var(--text-muted)" }}>
                  {formatRangeLabel(draft?.from ?? null, draft?.to ?? null)}
                </span>
                <div className="flex shrink-0 items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setDraft(undefined)}
                    className="rounded-md px-2.5 py-1 text-sm transition-colors hover:bg-[var(--surface-raised)]"
                    style={{ color: "var(--text-secondary)" }}
                  >
                    Reset
                  </button>
                  <button
                    type="button"
                    onClick={() => commit(draft)}
                    disabled={applyDisabled}
                    className={cn(
                      "rounded-md px-3 py-1 text-sm font-medium transition-colors",
                      "disabled:cursor-not-allowed disabled:opacity-50"
                    )}
                    style={{
                      // --accent-hover, not --accent: white on --accent measures
                      // 4.47:1, under the 4.5 AA floor. Same call the calendar's
                      // selected-day state makes; see calendar.tsx's header.
                      backgroundColor: "var(--accent-hover)",
                      color: "#ffffff",
                    }}
                  >
                    Apply
                  </button>
                </div>
              </div>
            </div>
          </div>
        </PopoverContent>
      </Popover>
    </div>
  );
}
