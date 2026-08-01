/**
 * EmailWorkspace's received-date filter STATE WIRING, exercised against the real
 * useEmailQueue / useQueueFacets hooks (only the network layer is mocked) so the
 * params asserted here are the ones actually built and sent.
 *
 * Three things this pins, each of which fails silently rather than loudly:
 *  1. the persisted values reach BOTH the queue call and the facets call;
 *  2. they are omitted entirely when unset (rather than sent as null/"");
 *  3. they participate in `filterKey`, so CHANGING the window resets the page.
 *
 * On (3): `filterKey` resets the page only when a filter changes DURING a
 * session — on mount it is deliberately seeded to the current key so a persisted
 * page survives returning to the same view. That is why this must be driven
 * through the real DateRangeFilter control rather than by seeding localStorage
 * or poking a setter: seeding only exercises the mount path, where `filterKey`
 * is inert by design. It is also why the clamp test at the bottom does NOT cover
 * this — mutation-checked, removing the dates from `filterKey` leaves the clamp
 * test green, because a small `total` resets the offset by a different mechanism.
 * The two tests guard different things and both must stay.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import QueuePage from "@/app/queue/page";

const api = vi.hoisted(() => ({
  getEmailQueue: vi.fn(),
  getQueueFacets: vi.fn(),
}));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getEmailQueue: api.getEmailQueue,
  getQueueFacets: api.getQueueFacets,
}));

// Only the two data hooks under test stay real.
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/hooks/useChairs", () => ({
  useChairs: () => ({ chairs: [], byId: new Map(), isLoading: false, isError: false }),
}));
vi.mock("@/hooks/useAppConfig", () => ({
  useAppConfig: () => ({ allowAutoSend: false }),
}));
vi.mock("@/hooks/useEmailQueueStream", () => ({
  useEmailQueueStream: () => ({ status: "live" }),
}));
vi.mock("@/hooks/useEmailThread", () => ({
  useEmailThread: () => ({ messages: [], isLoading: false, isError: false }),
}));

const AFTER_KEY = "confmail.filterReceivedAfter";
const BEFORE_KEY = "confmail.filterReceivedBefore";

function renderQueue() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <QueuePage />
    </QueryClientProvider>
  );
}

/** Args of the most recent call, so an initial unfiltered render can't mask it. */
function lastArgs(spy: { mock: { calls: unknown[][] } }) {
  return spy.mock.calls.at(-1)?.[0] as Record<string, unknown> | undefined;
}

beforeEach(() => {
  window.localStorage.clear();
  api.getEmailQueue.mockReset();
  api.getQueueFacets.mockReset();
  api.getEmailQueue.mockResolvedValue({ emails: [], total: 0 });
  api.getQueueFacets.mockResolvedValue({
    by_zendesk_status: {},
    by_source: {},
    sources: [],
  });
});

describe("received-date filter wiring", () => {
  it("sends neither param when the window is unset", async () => {
    renderQueue();

    await waitFor(() => expect(api.getEmailQueue).toHaveBeenCalled());
    await waitFor(() => expect(api.getQueueFacets).toHaveBeenCalled());

    // Absent, not present-and-null: a null would serialize into the query string
    // and the backend would reject or mis-handle it.
    expect(lastArgs(api.getEmailQueue)).not.toHaveProperty("received_after");
    expect(lastArgs(api.getEmailQueue)).not.toHaveProperty("received_before");
    expect(lastArgs(api.getQueueFacets)).not.toHaveProperty("received_after");
    expect(lastArgs(api.getQueueFacets)).not.toHaveProperty("received_before");
  });

  it("sends a persisted window on the QUEUE call", async () => {
    window.localStorage.setItem(AFTER_KEY, JSON.stringify("2026-07-21"));
    window.localStorage.setItem(BEFORE_KEY, JSON.stringify("2026-07-31"));
    renderQueue();

    await waitFor(() =>
      expect(lastArgs(api.getEmailQueue)).toMatchObject({
        received_after: "2026-07-21",
        received_before: "2026-07-31",
      })
    );
  });

  it("sends the same window on the FACETS call (context filter, not a facet dimension)", async () => {
    window.localStorage.setItem(AFTER_KEY, JSON.stringify("2026-07-21"));
    window.localStorage.setItem(BEFORE_KEY, JSON.stringify("2026-07-31"));
    renderQueue();

    await waitFor(() =>
      expect(lastArgs(api.getQueueFacets)).toMatchObject({
        received_after: "2026-07-21",
        received_before: "2026-07-31",
      })
    );
  });

  it("supports an open-ended window (one bound only)", async () => {
    window.localStorage.setItem(AFTER_KEY, JSON.stringify("2026-07-21"));
    renderQueue();

    await waitFor(() =>
      expect(lastArgs(api.getEmailQueue)).toMatchObject({ received_after: "2026-07-21" })
    );
    expect(lastArgs(api.getEmailQueue)).not.toHaveProperty("received_before");
  });

  it("passes bare YYYY-MM-DD through untouched — no time component is added", async () => {
    window.localStorage.setItem(AFTER_KEY, JSON.stringify("2026-07-21"));
    window.localStorage.setItem(BEFORE_KEY, JSON.stringify("2026-07-31"));
    renderQueue();

    await waitFor(() => expect(lastArgs(api.getEmailQueue)).toBeDefined());
    const args = lastArgs(api.getEmailQueue)!;
    // The backend owns whole-day expansion; a timestamp here would opt out of it
    // and silently drop most of the final day.
    expect(args.received_after).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(args.received_before).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it("changing the window via the real control resets the page to 1", async () => {
    // THE 2026-07-28 BUG CLASS. `total` is large enough that the stale-page
    // clamp cannot fire (18 pages exist at every point), so the ONLY thing that
    // can move the queue back to offset 0 is filterKey membership. Removing the
    // two dates from that array makes this test fail and nothing else.
    const user = userEvent.setup();
    api.getEmailQueue.mockResolvedValue({ emails: [], total: 1727 });
    window.localStorage.setItem("confmail.queuePage", JSON.stringify(4));
    renderQueue();

    // Sanity: we really are on page 4 (offset 300), not already at 0.
    await waitFor(() => expect(lastArgs(api.getEmailQueue)!.offset).toBe(300));

    // Drive the actual DateRangeFilter: open it, pick a preset, Apply.
    await user.click(screen.getByRole("button", { name: /Filter by received date/ }));
    await user.click(screen.getByRole("button", { name: "Last week" }));
    await user.click(screen.getByRole("button", { name: "Apply" }));

    await waitFor(() => {
      const args = lastArgs(api.getEmailQueue)!;
      expect(args).toHaveProperty("received_after");
      expect(args.offset).toBe(0);
    });
  });

  it("a persisted page beyond the windowed result set is clamped, not stranded", async () => {
    // Page 4 persisted from a previous, wider result set; the window now matches
    // a single page.
    window.localStorage.setItem("confmail.queuePage", JSON.stringify(4));
    window.localStorage.setItem(AFTER_KEY, JSON.stringify("2026-07-21"));
    window.localStorage.setItem(BEFORE_KEY, JSON.stringify("2026-07-31"));
    api.getEmailQueue.mockResolvedValue({ emails: [], total: 12 });
    renderQueue();

    await waitFor(() => expect(lastArgs(api.getEmailQueue)).toBeDefined());
    await waitFor(() => expect(lastArgs(api.getEmailQueue)!.offset).toBe(0));
  });
});
