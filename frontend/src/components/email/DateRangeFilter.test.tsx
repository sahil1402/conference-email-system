import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { format, subDays, subMonths } from "date-fns";
import { DateRangeFilter } from "@/components/email/DateRangeFilter";

const FMT = "yyyy-MM-dd";
const iso = (d: Date) => format(d, FMT);

function setup(after: string | null = null, before: string | null = null) {
  const onChange = vi.fn();
  render(
    <DateRangeFilter receivedAfter={after} receivedBefore={before} onChange={onChange} />
  );
  return { onChange, user: userEvent.setup() };
}

describe("DateRangeFilter", () => {
  beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }); vi.setSystemTime(new Date(2026, 6, 31)); });
  afterEach(() => vi.useRealTimers());

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

  it("collapses a single-day range and handles open ends", () => {
    setup("2026-07-21", "2026-07-21");
    expect(screen.getByText("Jul 21")).toBeInTheDocument();
  });

  it("Clear emits (null, null)", async () => {
    const { onChange, user } = setup("2026-07-21", "2026-07-31");
    await user.click(screen.getByRole("button", { name: "Clear" }));
    expect(onChange).toHaveBeenCalledWith(null, null);
  });

  it("presets emit bare YYYY-MM-DD pairs", async () => {
    const { onChange, user } = setup();
    const today = new Date(2026, 6, 31);

    for (const [label, expected] of [
      ["Today", [iso(today), iso(today)]],
      ["Yesterday", [iso(subDays(today, 1)), iso(subDays(today, 1))]],
      ["Last week", [iso(subDays(today, 6)), iso(today)]],
      ["Last month", [iso(subMonths(today, 1)), iso(today)]],
      ["Last quarter", [iso(subMonths(today, 3)), iso(today)]],
    ] as const) {
      onChange.mockClear();
      await user.click(screen.getByRole("button", { name: /Filter by received date/ }));
      await user.click(screen.getByRole("button", { name: label as string }));
      expect(onChange).toHaveBeenCalledWith(expected[0], expected[1]);
      for (const v of onChange.mock.calls[0]) {
        expect(v).toMatch(/^\d{4}-\d{2}-\d{2}$/);   // no time component, ever
      }
    }
  });

  it("round-trips a picked day without a timezone shift", async () => {
    const { onChange, user } = setup();
    await user.click(screen.getByRole("button", { name: /Filter by received date/ }));
    // The grid shows July 2026 (defaultMonth falls back to today).
    const cell = await screen.findByRole("button", { name: "Monday, July 20th, 2026" });
    await user.click(cell);
    expect(onChange).toHaveBeenCalled();
    const [a] = onChange.mock.calls[onChange.mock.calls.length - 1];
    expect(a).toBe("2026-07-20");   // the day clicked, not ±1
  });
});
