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

describe("EVIDENCE — windows placeholderData does not cover", () => {
  it("MOUNT with a persisted page: no previous data exists to keep", async () => {
    // A hard reload while on page 5. React Query's cache is empty, so there is
    // nothing for placeholderData to fall back to and total is 0 on the first
    // render — exactly the input the clamp misreads as "1 page".
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
    await screen.findByRole("button", { name: "Page 2" });

    const offsets = api.getEmailQueue.mock.calls.map((c) => c[0]?.offset);
    console.log(
      `[evidence] persisted page 5, cold cache → offsets requested: ${JSON.stringify(offsets)}, active page after load: ${activePage()}`
    );
    // Recorded, not asserted as desired — documents current behavior for the
    // clamp decision. A trailing offset 0 means the clamp reset the page.
    expect(offsets[0]).toBe(4 * QUEUE_PAGE_SIZE);
  });

  it("ERROR: a failed page fetch drops total to 0", async () => {
    const user = userEvent.setup();
    api.getEmailQueue.mockResolvedValue({ emails: makeEmails(5), total: LARGE_TOTAL });
    renderQueue();
    await screen.findByRole("button", { name: "Page 2" });

    api.getEmailQueue.mockRejectedValueOnce(new Error("network down"));
    await user.click(screen.getByRole("button", { name: "Page 2" }));

    // Let the rejection settle and any follow-on effect run.
    await new Promise((r) => setTimeout(r, 100));
    const offsets = api.getEmailQueue.mock.calls.map((c) => c[0]?.offset);
    console.log(
      `[evidence] errored page-2 fetch → offsets requested: ${JSON.stringify(offsets)}, active page: ${activePage()}`
    );
    // Recorded, not asserted as desired. A trailing offset 0 after the failed
    // page-2 fetch means the clamp reset the page on top of the error.
    expect(offsets.slice(0, 2)).toEqual([0, QUEUE_PAGE_SIZE]);
  });
});
