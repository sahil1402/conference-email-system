/**
 * EmailWorkspace's received-date filter STATE WIRING, exercised against the real
 * useEmailQueue / useQueueFacets hooks (only the network layer is mocked) so the
 * params asserted here are the ones actually built and sent.
 *
 * Two things this pins, both of which fail silently rather than loudly:
 *  1. the persisted values reach BOTH the queue call and the facets call;
 *  2. they are omitted entirely when unset (rather than sent as null/"").
 *
 * ⚠️ SCOPE LIMIT — `filterKey` membership is NOT covered here, and cannot be
 * until DateRangeFilter is rendered. `filterKey` resets the page only when a
 * filter CHANGES during a session; on mount it is deliberately seeded to the
 * current key so a persisted page survives returning to the same view. With no
 * control rendered there is no way to change the window mid-session, and
 * `usePersistedState` reads localStorage once at init. Mutation-checked: removing
 * the two dates from the `filterKey` array fails NOTHING in this file — the
 * clamp test below passes either way, because a small `total` makes the
 * stale-page clamp reset the offset regardless. The code is correct (both values
 * ARE in `filterKey`); it is the coverage that is deferred. **Add a
 * change-the-window-then-assert-page-1 test with the render step**, or the
 * 2026-07-28 pagination bug class can regress here unnoticed.
 *
 * DateRangeFilter is deliberately not rendered yet (state wiring only), so the
 * window is seeded through localStorage — which is exactly how a persisted
 * filter reaches the workspace on mount anyway.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, waitFor } from "@testing-library/react";
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
