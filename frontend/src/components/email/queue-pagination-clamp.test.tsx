/**
 * The stale-page clamp in EmailWorkspace, exercised against the REAL
 * useEmailQueue hook (only the network layer is mocked) so `total`'s
 * pending/success/error transitions are produced by React Query itself.
 *
 * The clamp exists for a genuine case — the current page no longer existing
 * because the result set shrank. It must fire there, and must NOT fire merely
 * because a page's data has not arrived yet.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import QueuePage from "@/app/queue/page";
import { QUEUE_PAGE_SIZE } from "@/lib/api";

const api = vi.hoisted(() => ({ getEmailQueue: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getEmailQueue: api.getEmailQueue,
}));

// Everything EXCEPT useEmailQueue is stubbed — that hook is the subject.
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/hooks/useQueueFacets", () => ({
  useQueueFacets: () => ({
    byZendeskStatus: {}, bySource: {}, sources: [], isLoading: false, isError: false,
  }),
}));
vi.mock("@/hooks/useChairs", () => ({
  useChairs: () => ({ chairs: [], byId: new Map(), isLoading: false, isError: false }),
}));
vi.mock("@/hooks/useAppConfig", () => ({ useAppConfig: () => ({ allowAutoSend: false }) }));
vi.mock("@/hooks/useEmailQueueStream", () => ({
  useEmailQueueStream: () => ({ status: "live" }),
}));
vi.mock("@/hooks/useEmailThread", () => ({
  useEmailThread: () => ({ messages: [], isLoading: false, isError: false }),
}));

function makeEmails(n: number, offset = 0) {
  return Array.from({ length: n }, (_, i) => ({
    id: offset + i + 1,
    sender: `u${offset + i}@x.edu`,
    sender_name: `User ${offset + i}`,
    subject: `Subject ${offset + i}`,
    body: "b",
    status: "DRAFT_GENERATED",
    received_at: "2026-07-20T09:00:00Z",
    assigned_chair_id: null,
    source: "zendesk",
    zendesk_ticket_id: 1000 + offset + i,
    zendesk_status: "open",
    classification: null,
    routing: { lane: "human_review" },
    draft: null,
    created_at: "2026-07-20T09:00:00Z",
    updated_at: "2026-07-20T09:00:00Z",
  }));
}

/** Which page the UI currently considers active, per the pagination controls. */
function activePage(): number {
  const current = document.querySelector('[aria-current="page"]');
  return Number(current?.textContent ?? NaN);
}

function renderQueue() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <QueuePage />
    </QueryClientProvider>
  );
}

/** A never-settling fetch, so the in-flight window stays observable. */
function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

type QueueResult = { emails: unknown[]; total: number };
const LARGE_TOTAL = 1727; // 18 pages at 100/page

beforeEach(() => {
  window.localStorage.clear();
  api.getEmailQueue.mockReset();
});

describe("clamp must NOT fire on a page whose data has not arrived", () => {
  it("clicking an uncached page number does not bounce back to page 1", async () => {
    const user = userEvent.setup();
    api.getEmailQueue.mockResolvedValue({ emails: makeEmails(5), total: LARGE_TOTAL });
    renderQueue();
    await screen.findByRole("button", { name: "Page 2" });

    // Page 2 has never been fetched; hold its response open so the transient
    // window is real and observable.
    const pending = deferred<QueueResult>();
    api.getEmailQueue.mockReturnValueOnce(pending.promise);

    await user.click(screen.getByRole("button", { name: "Page 2" }));

    // THE BUG: total used to collapse to 0 here → pageCount 1 → clamp to 1.
    expect(activePage()).toBe(2);

    pending.resolve({ emails: makeEmails(5, 100), total: LARGE_TOTAL });
    await waitFor(() =>
      expect(api.getEmailQueue).toHaveBeenCalledWith(
        expect.objectContaining({ offset: QUEUE_PAGE_SIZE })
      )
    );
    expect(activePage()).toBe(2);
  });

  it("the Next chevron into an uncached page behaves identically", async () => {
    const user = userEvent.setup();
    api.getEmailQueue.mockResolvedValue({ emails: makeEmails(5), total: LARGE_TOTAL });
    renderQueue();
    await screen.findByRole("button", { name: "Page 2" });

    const pending = deferred<QueueResult>();
    api.getEmailQueue.mockReturnValueOnce(pending.promise);

    await user.click(screen.getByRole("button", { name: "Next page" }));

    expect(activePage()).toBe(2);
    pending.resolve({ emails: makeEmails(5, 100), total: LARGE_TOTAL });
    await waitFor(() => expect(activePage()).toBe(2));
  });

  it("deeper paging keeps working across successive uncached pages", async () => {
    const user = userEvent.setup();
    api.getEmailQueue.mockResolvedValue({ emails: makeEmails(5), total: LARGE_TOTAL });
    renderQueue();
    await screen.findByRole("button", { name: "Page 2" });

    for (const target of [2, 3, 4]) {
      await user.click(screen.getByRole("button", { name: `Page ${target}` }));
      await waitFor(() => expect(activePage()).toBe(target));
    }
  });
});

describe("clamp must NOT fire when the count is simply unknown", () => {
  it("a persisted page survives a COLD-CACHE mount (hard reload)", async () => {
    // Second defect, distinct from the click bug: on a hard reload React
    // Query's cache is empty, so placeholderData has nothing to fall back to
    // and total is 0 on the first render — which the ungated clamp read as
    // "1 page" and used to reset page 5 → 1, re-fetching offset 0.
    window.localStorage.setItem("confmail.queuePage", "5");
    const pending = deferred<QueueResult>();
    let call = 0;
    api.getEmailQueue.mockImplementation(() => {
      call += 1;
      // Hold only the FIRST fetch open, so the cold-cache window is observable.
      return call === 1
        ? pending.promise
        : Promise.resolve({ emails: makeEmails(5), total: LARGE_TOTAL });
    });

    renderQueue();
    await waitFor(() => expect(api.getEmailQueue).toHaveBeenCalled());
    pending.resolve({ emails: makeEmails(5, 400), total: LARGE_TOTAL });
    // On page 5 of 18 the controls read "1 … 4 5 6 … 18" — no "Page 2" exists
    // here, which is itself the point: a reset to page 1 would have rendered it.
    await screen.findByRole("button", { name: "Page 5" });

    // Page 5 is honored and STAYS honored: one fetch, at its offset. A trailing
    // offset-0 call would mean the clamp had reset the page.
    const offsets = api.getEmailQueue.mock.calls.map((c) => c[0]?.offset);
    expect(offsets).toEqual([4 * QUEUE_PAGE_SIZE]);
    expect(activePage()).toBe(5);
  });

  it("a FAILED page fetch leaves the user where they are", async () => {
    const user = userEvent.setup();
    api.getEmailQueue.mockResolvedValue({ emails: makeEmails(5), total: LARGE_TOTAL });
    renderQueue();
    await screen.findByRole("button", { name: "Page 2" });

    api.getEmailQueue.mockRejectedValueOnce(new Error("network down"));
    await user.click(screen.getByRole("button", { name: "Page 2" }));

    // Let the rejection settle and any follow-on effect run.
    await new Promise((r) => setTimeout(r, 100));

    // No offset-0 re-fetch after the failed page-2 fetch: the error is surfaced
    // without also yanking the user back to page 1 (total 0 here means "the
    // fetch failed", not "there is one page").
    const offsets = api.getEmailQueue.mock.calls.map((c) => c[0]?.offset);
    expect(offsets).toEqual([0, QUEUE_PAGE_SIZE]);
  });
});

describe("clamp must STILL fire when the set genuinely shrinks", () => {
  it("clamps to the last real page when the count drops below the current page", async () => {
    // The case the effect exists for: sitting on a late page when the result
    // set shrinks under you (tickets resolved out of the filter on the 15s
    // poll). Simulated at mount — a persisted page 5 against a set that now
    // holds only 150 rows (2 pages) — which is the same input the effect sees.
    window.localStorage.setItem("confmail.queuePage", "5");
    api.getEmailQueue.mockResolvedValue({ emails: makeEmails(5), total: 150 });

    renderQueue();

    // Page 5 no longer exists → clamp to the last real page, not to page 1.
    await waitFor(() => expect(activePage()).toBe(2));
    await waitFor(() =>
      expect(api.getEmailQueue).toHaveBeenLastCalledWith(
        expect.objectContaining({ offset: QUEUE_PAGE_SIZE })
      )
    );
    expect(screen.queryByRole("button", { name: "Page 5" })).toBeNull();
  });

  it("clamps down to page 1 when the set shrinks to a single page", async () => {
    window.localStorage.setItem("confmail.queuePage", "5");
    api.getEmailQueue.mockResolvedValue({ emails: makeEmails(3), total: 3 });

    renderQueue();

    // One page → the controls hide entirely, so assert via the fetch offset.
    await waitFor(() =>
      expect(api.getEmailQueue).toHaveBeenLastCalledWith(
        expect.objectContaining({ offset: 0 })
      )
    );
  });
});
