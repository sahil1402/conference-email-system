import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { format, subDays, subMonths } from "date-fns";

import { DateRangeFilter } from "@/components/email/DateRangeFilter";

/**
 * The wire format IS the contract with the backend: bare `YYYY-MM-DD`, which the
 * server expands to whole-day bounds. Anything with a time component would opt
 * out of that expansion and silently drop most of the final day, so the format
 * is asserted with a regex on every emission rather than trusted.
 *
 * The draft/Apply behaviour is the other half: the first implementation
 * committed on every calendar click, which made range-building unusable. These
 * tests pin that a click is NOT a commit.
 *
 * SCOPE LIMIT — jsdom performs no layout, so nothing here proves the popover is
 * positioned or sized usably at the 256px sidebar width. That needs a human.
 */

const FMT = "yyyy-MM-dd";
const iso = (d: Date) => format(d, FMT);
const TODAY = new Date(2026, 6, 31); // frozen clock: Fri 31 Jul 2026
const TRIGGER = /Filter by received date/;

function setup(after: string | null = null, before: string | null = null) {
  const onChange = vi.fn();
  render(
    <DateRangeFilter receivedAfter={after} receivedBefore={before} onChange={onChange} />
  );
  return { onChange, user: userEvent.setup({ advanceTimers: vi.advanceTimersByTime }) };
}

const openPopover = (user: ReturnType<typeof userEvent.setup>) =>
  user.click(screen.getByRole("button", { name: TRIGGER }));

describe("DateRangeFilter", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(TODAY);
  });
  afterEach(() => vi.useRealTimers());

  // --- trigger label + header Clear ---------------------------------------

  it("shows a neutral label and no Clear when unset", () => {
    setup();
    expect(screen.getByText("All dates")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Clear" })).toBeNull();
  });

  it("formats an active range and offers Clear", () => {
    setup("2026-07-21", "2026-07-31");
    expect(screen.getByText("Jul 21 – Jul 31")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clear" })).toBeInTheDocument();
  });

  it("collapses a single-day range to one label", () => {
    setup("2026-07-21", "2026-07-21");
    expect(screen.getByText("Jul 21")).toBeInTheDocument();
  });

  it("header Clear commits immediately", async () => {
    const { onChange, user } = setup("2026-07-21", "2026-07-31");
    await user.click(screen.getByRole("button", { name: "Clear" }));
    expect(onChange).toHaveBeenCalledWith(null, null);
  });

  // --- draft semantics: a click is NOT a commit ---------------------------

  it("selecting a date does NOT call onChange and leaves the popover open", async () => {
    const { onChange, user } = setup();
    await openPopover(user);

    await user.click(screen.getByRole("button", { name: "Monday, July 20th, 2026" }));

    expect(onChange).not.toHaveBeenCalled();
    // Still open: Apply is what closes it.
    expect(screen.getByRole("button", { name: "Apply" })).toBeInTheDocument();
  });

  it("building a two-click range still does not commit until Apply", async () => {
    const { onChange, user } = setup();
    await openPopover(user);

    await user.click(screen.getByRole("button", { name: "Monday, July 20th, 2026" }));
    await user.click(screen.getByRole("button", { name: "Friday, July 24th, 2026" }));
    expect(onChange).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Apply" }));
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith("2026-07-20", "2026-07-24");
  });

  it("Apply on a single picked day commits a one-day range", async () => {
    const { onChange, user } = setup();
    await openPopover(user);
    await user.click(screen.getByRole("button", { name: "Monday, July 20th, 2026" }));
    await user.click(screen.getByRole("button", { name: "Apply" }));

    expect(onChange).toHaveBeenCalledWith("2026-07-20", "2026-07-20");
  });

  it("Apply with no edits re-commits the currently applied range", async () => {
    const { onChange, user } = setup("2026-07-21", "2026-07-31");
    await openPopover(user);
    await user.click(screen.getByRole("button", { name: "Apply" }));

    expect(onChange).toHaveBeenCalledWith("2026-07-21", "2026-07-31");
  });

  it("Apply closes the popover", async () => {
    const { user } = setup();
    await openPopover(user);
    await user.click(screen.getByRole("button", { name: "Monday, July 20th, 2026" }));
    await user.click(screen.getByRole("button", { name: "Apply" }));

    expect(screen.queryByRole("button", { name: "Apply" })).toBeNull();
  });

  it("Apply is disabled only when there is nothing to apply", async () => {
    const { user } = setup();
    await openPopover(user);
    expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Monday, July 20th, 2026" }));
    expect(screen.getByRole("button", { name: "Apply" })).toBeEnabled();
  });

  // --- cancelling discards the draft --------------------------------------

  it("clicking away without Apply discards the draft and does not commit", async () => {
    const { onChange, user } = setup();
    await openPopover(user);
    await user.click(screen.getByRole("button", { name: "Monday, July 20th, 2026" }));

    await user.keyboard("{Escape}");

    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByText("All dates")).toBeInTheDocument();
  });

  it("reopening shows the APPLIED range, never a stale abandoned draft", async () => {
    const { onChange, user } = setup("2026-07-21", "2026-07-31");

    // Abandon a different selection.
    await openPopover(user);
    await user.click(screen.getByRole("button", { name: "Monday, July 6th, 2026" }));
    await user.keyboard("{Escape}");
    expect(onChange).not.toHaveBeenCalled();

    // Reopen and Apply without touching anything: the committed range must come
    // back, NOT the abandoned Jul 6 draft.
    await openPopover(user);
    await user.click(screen.getByRole("button", { name: "Apply" }));
    expect(onChange).toHaveBeenCalledWith("2026-07-21", "2026-07-31");
  });

  // --- Reset is a DRAFT action --------------------------------------------

  it("Reset clears the draft without committing or closing", async () => {
    const { onChange, user } = setup("2026-07-21", "2026-07-31");
    await openPopover(user);

    await user.click(screen.getByRole("button", { name: "Reset" }));

    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Apply" })).toBeInTheDocument();
  });

  it("Reset then Apply commits the cleared filter", async () => {
    const { onChange, user } = setup("2026-07-21", "2026-07-31");
    await openPopover(user);

    await user.click(screen.getByRole("button", { name: "Reset" }));
    await user.click(screen.getByRole("button", { name: "Apply" }));

    expect(onChange).toHaveBeenCalledWith(null, null);
  });

  // --- presets populate the draft -----------------------------------------

  it("a preset fills the draft without committing or closing", async () => {
    const { onChange, user } = setup();
    await openPopover(user);

    await user.click(screen.getByRole("button", { name: "Today" }));

    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Apply" })).toBeInTheDocument();
  });

  it.each([
    ["Today", [iso(TODAY), iso(TODAY)]],
    ["Yesterday", [iso(subDays(TODAY, 1)), iso(subDays(TODAY, 1))]],
    ["Last week", [iso(subDays(TODAY, 6)), iso(TODAY)]],
    ["Last month", [iso(subMonths(TODAY, 1)), iso(TODAY)]],
    ["Last quarter", [iso(subMonths(TODAY, 3)), iso(TODAY)]],
  ])("preset %s applies as a bare YYYY-MM-DD pair", async (label, expected) => {
    const { onChange, user } = setup();
    await openPopover(user);
    await user.click(screen.getByRole("button", { name: label }));
    await user.click(screen.getByRole("button", { name: "Apply" }));

    expect(onChange).toHaveBeenCalledWith(expected[0], expected[1]);
    for (const value of onChange.mock.calls[0]) {
      // No time component may ever reach the wire — the backend owns whole-day
      // expansion and a timestamp would silently opt out of it.
      expect(value).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    }
  });

  // --- timezone round-trip -------------------------------------------------

  it("round-trips a picked day without a timezone shift", async () => {
    const { onChange, user } = setup();
    await openPopover(user);
    await user.click(screen.getByRole("button", { name: "Monday, July 20th, 2026" }));
    await user.click(screen.getByRole("button", { name: "Apply" }));

    // `toISOString().slice(0,10)` would send the 19th east of UTC; `new Date(str)`
    // would render the 19th west of it. The day clicked must survive both.
    expect(onChange).toHaveBeenCalledWith("2026-07-20", "2026-07-20");
  });
});
