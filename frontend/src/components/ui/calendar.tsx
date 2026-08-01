"use client";

import * as React from "react";
import {
  DayPicker,
  getDefaultClassNames,
  type DayButton,
  type Locale,
} from "react-day-picker";

import { cn } from "@/lib/utils";
import { Button, buttonVariants } from "@/components/ui/button";
import { ChevronLeftIcon, ChevronRightIcon, ChevronDownIcon } from "lucide-react";

/**
 * shadcn/ui Calendar (react-day-picker), adapted to this app's design tokens.
 *
 * TWO independent rewrites were needed on the generated file, either of which
 * alone would have left it broken:
 *
 * 1. COLOR. Like button.tsx / dialog.tsx / dropdown-menu.tsx / popover.tsx, the
 *    stock classes (bg-muted / text-muted-foreground / bg-primary /
 *    text-primary-foreground / bg-popover / border-ring …) reference CSS vars
 *    this project does not define, so they render colorless. Every one is mapped
 *    to a real token from globals.css.
 *
 * 2. TAILWIND VERSION. The registry emits Tailwind v4 syntax — `size-(--cell-size)`,
 *    `rounded-(--cell-radius)`, `--spacing(7)`, `in-data-[slot=…]` — and this
 *    project is on Tailwind 3.4. Those compile to NOTHING in v3, so cell sizing
 *    and corner radii silently vanished. Rewritten to v3 arbitrary values
 *    (`size-[var(--cell-size)]`, `rounded-[var(--cell-radius)]`), with the two
 *    custom properties set inline on the root rather than via a v4 utility.
 *
 * Token choices follow the color pattern of button.tsx (Tailwind arbitrary
 * values referencing vars) rather than popover.tsx's inline `style` object:
 * react-day-picker styles per-STATE through `classNames`, and an inline style
 * cannot express "selected" vs "range middle" vs "outside month".
 *
 * Day-state map:
 *   default          text-primary, transparent
 *   hover            surface-raised            (matches the ghost button)
 *   today            accent RING, normal text  (see the contrast note below)
 *   selected (single) accent-hover fill, white text  (see SELECTED FILL below)
 *   range start/end  accent-hover fill, white text
 *   range middle     25% accent mix, text-primary  (see the range note below)
 *   outside month    text-muted ON THE BUTTON (see the note in `outside`)
 *   disabled         text-muted @ 50%
 *
 * ⚠️ RANGE FILL — why NOT --accent-subtle, which is the obvious choice.
 * --accent-subtle is tuned to sit on --background/--surface, but this calendar
 * renders inside a popover painted --surface-raised, and against THAT it is
 * effectively invisible: 1.07:1 in dark (#1e1f3a on #21263a — identical blue
 * channel, R/G within 7) and 1.01:1 in light. The selected range would have had
 * no visible band at all. Replaced with a 25% --accent mix over the surface,
 * which stays token-derived (no hardcoded hex) and separates by HUE as well as
 * luminance — the reason the measured ratio (~1.33) understates how visible it
 * actually is, and it is anchored at both ends by solid accent endpoints.
 * Requires `color-mix()` (Baseline 2023; fine for this app's targets).
 *
 * ⚠️ SELECTED FILL — why --accent-hover rather than the obvious --accent.
 * White text on --accent (#6366f1) measures 4.47:1 in BOTH themes (the two
 * accent tokens are the same hex in dark and light), just under the 4.5:1 AA
 * floor for body text. --accent-hover (#4f46e5) measures 6.29:1. Since a
 * selected day number is text a chair has to read, this is an accessibility
 * floor rather than a palette choice. Hover then darkens further via an 85%
 * mix toward black (7.86:1) so the affordance survives the base getting darker.
 * NOTE this deliberately does NOT match button.tsx's primary variant, which
 * still ships white-on---accent at 4.47:1 app-wide; that is tracked separately
 * and is not this component's to change.
 *
 * ⚠️ CONTRAST — why "today" is a ring and not accent-colored text. `--accent`
 * (#6366f1) is identical in both themes, and as TEXT on the light surface
 * (#ffffff) it measures ~4.1:1 — below the 4.5:1 AA floor for body text. That is
 * exactly the CHAIR_PALETTE trap (pastel accents that passed on dark and failed
 * as text on white). Using the accent only as a 1px ring keeps it a non-text UI
 * affordance (3:1 threshold, which it clears in both themes) and leaves the day
 * number at --text-primary, which is near-maximal contrast in both. Do not
 * "simplify" today into `text-[var(--accent)]`.
 */

/** Cell geometry. Set inline (not via a Tailwind v4 `--spacing()` utility) so
 *  the arbitrary-value classes below resolve under Tailwind 3. */
const CALENDAR_VARS = {
  "--cell-size": "2rem",
  "--cell-radius": "0.5rem",
} as React.CSSProperties;

function Calendar({
  className,
  classNames,
  showOutsideDays = true,
  captionLayout = "label",
  buttonVariant = "ghost",
  locale,
  formatters,
  components,
  style,
  ...props
}: React.ComponentProps<typeof DayPicker> & {
  buttonVariant?: React.ComponentProps<typeof Button>["variant"];
}) {
  const defaultClassNames = getDefaultClassNames();

  return (
    <DayPicker
      showOutsideDays={showOutsideDays}
      // Background is deliberately TRANSPARENT rather than --surface: this is
      // designed to sit inside PopoverContent, which already paints
      // --surface-raised. Painting again would create a visible seam.
      className={cn("group/calendar bg-transparent p-2", className)}
      style={{ ...CALENDAR_VARS, ...style }}
      captionLayout={captionLayout}
      locale={locale}
      formatters={{
        formatMonthDropdown: (date) =>
          date.toLocaleString(locale?.code, { month: "short" }),
        ...formatters,
      }}
      classNames={{
        root: cn("w-fit", defaultClassNames.root),
        months: cn(
          "relative flex flex-col gap-4 md:flex-row",
          defaultClassNames.months
        ),
        month: cn("flex w-full flex-col gap-4", defaultClassNames.month),
        nav: cn(
          "absolute inset-x-0 top-0 flex w-full items-center justify-between gap-1",
          defaultClassNames.nav
        ),
        button_previous: cn(
          buttonVariants({ variant: buttonVariant }),
          "size-[var(--cell-size)] p-0 select-none aria-disabled:opacity-50",
          defaultClassNames.button_previous
        ),
        button_next: cn(
          buttonVariants({ variant: buttonVariant }),
          "size-[var(--cell-size)] p-0 select-none aria-disabled:opacity-50",
          defaultClassNames.button_next
        ),
        month_caption: cn(
          "flex h-[var(--cell-size)] w-full items-center justify-center px-[var(--cell-size)]",
          defaultClassNames.month_caption
        ),
        dropdowns: cn(
          "flex h-[var(--cell-size)] w-full items-center justify-center gap-1.5 text-sm font-medium",
          defaultClassNames.dropdowns
        ),
        dropdown_root: cn(
          "relative rounded-[var(--cell-radius)]",
          defaultClassNames.dropdown_root
        ),
        dropdown: cn(
          "absolute inset-0 bg-[var(--surface-raised)] opacity-0",
          defaultClassNames.dropdown
        ),
        caption_label: cn(
          "select-none font-medium text-[var(--text-primary)]",
          captionLayout === "label"
            ? "text-sm"
            : "flex items-center gap-1 rounded-[var(--cell-radius)] text-sm [&>svg]:size-3.5 [&>svg]:text-[var(--text-muted)]",
          defaultClassNames.caption_label
        ),
        month_grid: cn("w-full border-collapse", defaultClassNames.month_grid),
        weekdays: cn("flex", defaultClassNames.weekdays),
        weekday: cn(
          // --text-secondary, NOT --text-muted: the weekday initials are
          // navigational labels a user must actually read, and muted measures
          // 2.27:1 (dark) / 2.29:1 (light) against the popover surface — well
          // under AA. Secondary measures 4.78 / 5.38 and passes in both themes.
          "flex-1 select-none rounded-[var(--cell-radius)] text-[0.8rem] font-normal text-[var(--text-secondary)]",
          defaultClassNames.weekday
        ),
        week: cn("mt-2 flex w-full", defaultClassNames.week),
        week_number_header: cn(
          "w-[var(--cell-size)] select-none",
          defaultClassNames.week_number_header
        ),
        week_number: cn(
          "select-none text-[0.8rem] text-[var(--text-muted)]",
          defaultClassNames.week_number
        ),
        day: cn(
          "group/day relative aspect-square h-full w-full select-none rounded-[var(--cell-radius)] p-0 text-center",
          "[&:last-child[data-selected=true]_button]:rounded-r-[var(--cell-radius)]",
          props.showWeekNumber
            ? "[&:nth-child(2)[data-selected=true]_button]:rounded-l-[var(--cell-radius)]"
            : "[&:first-child[data-selected=true]_button]:rounded-l-[var(--cell-radius)]",
          defaultClassNames.day
        ),
        // The cell-level range tint. The `after:` bleed fills the gap between
        // adjacent cells so a selected range reads as one continuous band.
        range_start: cn(
          "relative isolate z-0 rounded-l-[var(--cell-radius)] bg-[color-mix(in_srgb,var(--accent)_25%,transparent)]",
          "after:absolute after:inset-y-0 after:right-0 after:w-4 after:bg-[color-mix(in_srgb,var(--accent)_25%,transparent)]",
          defaultClassNames.range_start
        ),
        range_middle: cn(
          "rounded-none bg-[color-mix(in_srgb,var(--accent)_25%,transparent)]",
          defaultClassNames.range_middle
        ),
        range_end: cn(
          "relative isolate z-0 rounded-r-[var(--cell-radius)] bg-[color-mix(in_srgb,var(--accent)_25%,transparent)]",
          "after:absolute after:inset-y-0 after:left-0 after:w-4 after:bg-[color-mix(in_srgb,var(--accent)_25%,transparent)]",
          defaultClassNames.range_end
        ),
        today: cn(
          // Ring, not fill or colored text — see the contrast note in the header.
          "rounded-[var(--cell-radius)] ring-1 ring-inset ring-[var(--accent)]",
          "data-[selected=true]:rounded-none data-[selected=true]:ring-0",
          defaultClassNames.today
        ),
        outside: cn(
          // ⚠️ Targets the BUTTON, not the cell. The previous
          // `text-[var(--text-muted)]` sat on the <td> and never took effect:
          // CalendarDayButton sets its own explicit text color, and an
          // explicitly-set color on a child ALWAYS beats a color inherited from
          // its parent, whatever the selectors' specificity. Outside-month days
          // therefore rendered at --text-primary — identical to in-month days,
          // which is exactly how the "they look the same" report presented.
          // The descendant form also out-specifies the button's own utility
          // (0,1,1 vs 0,1,0), so it wins on both counts.
          "[&_button]:text-[var(--text-muted)]",
          // Hover lifts to secondary rather than the ghost variant's primary:
          // enough feedback to confirm these are clickable, without letting an
          // outside day briefly read as in-month.
          "[&_button:hover]:text-[var(--text-secondary)]",
          defaultClassNames.outside
        ),
        disabled: cn(
          "text-[var(--text-muted)] opacity-50",
          defaultClassNames.disabled
        ),
        hidden: cn("invisible", defaultClassNames.hidden),
        ...classNames,
      }}
      components={{
        Root: ({ className, rootRef, ...props }) => (
          <div
            data-slot="calendar"
            ref={rootRef}
            className={cn(className)}
            {...props}
          />
        ),
        Chevron: ({ className, orientation, ...props }) => {
          if (orientation === "left") {
            return (
              <ChevronLeftIcon className={cn("size-4", className)} {...props} />
            );
          }
          if (orientation === "right") {
            return (
              <ChevronRightIcon className={cn("size-4", className)} {...props} />
            );
          }
          return <ChevronDownIcon className={cn("size-4", className)} {...props} />;
        },
        DayButton: ({ ...props }) => (
          <CalendarDayButton locale={locale} {...props} />
        ),
        WeekNumber: ({ children, ...props }) => (
          <td {...props}>
            <div className="flex size-[var(--cell-size)] items-center justify-center text-center">
              {children}
            </div>
          </td>
        ),
        ...components,
      }}
      {...props}
    />
  );
}

function CalendarDayButton({
  className,
  day,
  modifiers,
  locale,
  ...props
}: React.ComponentProps<typeof DayButton> & { locale?: Partial<Locale> }) {
  const defaultClassNames = getDefaultClassNames();

  const ref = React.useRef<HTMLButtonElement>(null);
  React.useEffect(() => {
    if (modifiers.focused) ref.current?.focus();
  }, [modifiers.focused]);

  return (
    <Button
      ref={ref}
      variant="ghost"
      size="icon"
      data-day={day.date.toLocaleDateString(locale?.code)}
      data-selected-single={
        modifiers.selected &&
        !modifiers.range_start &&
        !modifiers.range_end &&
        !modifiers.range_middle
      }
      data-range-start={modifiers.range_start}
      data-range-end={modifiers.range_end}
      data-range-middle={modifiers.range_middle}
      className={cn(
        // Layout. `size-auto` overrides the ghost button's fixed h-10/w-10 so the
        // day fills its cell.
        "relative isolate z-10 flex aspect-square w-full min-w-[var(--cell-size)] size-auto flex-col gap-1 border-0 font-normal leading-none",
        "text-[var(--text-primary)] hover:bg-[var(--surface-raised)]",
        // Focus ring, token-mapped (stock used border-ring / ring-ring).
        "group-data-[focused=true]/day:relative group-data-[focused=true]/day:z-10",
        "group-data-[focused=true]/day:ring-2 group-data-[focused=true]/day:ring-[var(--accent)]",
        // Single selection. Fill is --accent-HOVER, not --accent: white on
        // --accent measures 4.47:1, just under the 4.5 AA floor for body text,
        // while --accent-hover measures 6.29:1. This is an accessibility floor,
        // not a style preference — see the SELECTED FILL note in the header.
        "data-[selected-single=true]:bg-[var(--accent-hover)] data-[selected-single=true]:text-white",
        "data-[selected-single=true]:hover:bg-[color-mix(in_srgb,var(--accent-hover)_85%,black)]",
        // Range ends: same solid treatment as a single selection, so they must
        // use the same fill — leaving these at --accent would both fail AA and
        // render the endpoints a visibly different indigo from a single pick.
        "data-[range-start=true]:rounded-l-[var(--cell-radius)] data-[range-start=true]:bg-[var(--accent-hover)] data-[range-start=true]:text-white",
        "data-[range-start=true]:hover:bg-[color-mix(in_srgb,var(--accent-hover)_85%,black)]",
        "data-[range-end=true]:rounded-r-[var(--cell-radius)] data-[range-end=true]:bg-[var(--accent-hover)] data-[range-end=true]:text-white",
        "data-[range-end=true]:hover:bg-[color-mix(in_srgb,var(--accent-hover)_85%,black)]",
        // Range middle: the cell already paints --accent-subtle, so the button
        // stays transparent and only carries the text color.
        "data-[range-middle=true]:rounded-none data-[range-middle=true]:bg-transparent data-[range-middle=true]:text-[var(--text-primary)]",
        "[&>span]:text-xs [&>span]:opacity-70",
        defaultClassNames.day,
        className
      )}
      {...props}
    />
  );
}

export { Calendar, CalendarDayButton };
