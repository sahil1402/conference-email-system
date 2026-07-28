/**
 * Step-5 pagination behaviors on the real QueuePage → EmailWorkspace:
 *  1. Changing a filter resets to page 1.
 *  2. Each page keeps its own remembered scroll position (page IS part of the
 *     scroll-restoration key) — a new page opens at the top, returning to a page
 *     restores where you left it.
 *
 * SCOPE LIMIT — read before adding pagination coverage here. This file mocks
 * useEmailQueue to return a CONSTANT total regardless of params, so `total` is
 * never absent. The real hook returns no data at all for a key it has not
 * fetched yet, and that transient window (total 0 → pageCount 1 → the clamp
 * resets the page) was a live bug this file could not see. Anything that
 * depends on data ARRIVING — loading windows, the clamp, cold-cache mounts,
 * fetch errors — belongs in queue-pagination-clamp.test.tsx, which mocks only
 * the network layer and drives the real hook.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import QueuePage from "@/app/queue/page";
import { QUEUE_PAGE_SIZE } from "@/lib/api";
import type { EmailQueueParams } from "@/lib/api";
import type { Email } from "@/types";

const cap = vi.hoisted(() => ({
  params: null as EmailQueueParams | null,
  total: 0,
  emails: [] as Email[],
}));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/hooks/useEmailQueue", () => ({
  useEmailQueue: (params: EmailQueueParams) => {
    cap.params = params;
    return { emails: cap.emails, total: cap.total, isLoading: false, isError: false, refetch: vi.fn() };
  },
}));
vi.mock("@/hooks/useQueueFacets", () => ({
  useQueueFacets: () => ({ byZendeskStatus: {}, bySource: {}, sources: [], isLoading: false, isError: false }),
}));
vi.mock("@/hooks/useChairs", () => ({
  useChairs: () => ({ chairs: [], byId: new Map(), isLoading: false, isError: false }),
}));
vi.mock("@/hooks/useAppConfig", () => ({ useAppConfig: () => ({ allowAutoSend: false }) }));
vi.mock("@/hooks/useEmailQueueStream", () => ({ useEmailQueueStream: () => ({ status: "live" }) }));
vi.mock("@/hooks/useEmailThread", () => ({
  useEmailThread: () => ({ messages: [], isLoading: false, isError: false }),
}));

function makeEmails(n: number): Email[] {
  return Array.from({ length: n }, (_, i) => ({
    id: i + 1,
    sender: `u${i}@x.edu`,
    sender_name: `User ${i}`,
    subject: `Subject ${i}`,
    body: "b",
    status: "DRAFT_GENERATED",
    received_at: "2026-07-20T09:00:00Z",
    assigned_chair_id: null,
    source: "zendesk",
    zendesk_ticket_id: 1000 + i,
    zendesk_status: "open",
    classification: null,
    routing: { lane: "human_review" },
    draft: null,
    created_at: "2026-07-20T09:00:00Z",
    updated_at: "2026-07-20T09:00:00Z",
  })) as unknown as Email[];
}

function renderQueue() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <QueuePage />
    </QueryClientProvider>
  );
}

/** Give an element a real, settable scrollTop (jsdom's is a no-op). */
function installScrollTop(el: HTMLElement) {
  let v = 0;
  Object.defineProperty(el, "scrollTop", {
    configurable: true,
    get: () => v,
    set: (n: number) => {
      v = n;
    },
  });
}

/** The list's scrollable container (flex-1 + overflow-y-auto — the filter
 * column is shrink-0, the detail pane is overflow-hidden, so this is unique). */
const listContainer = () =>
  document.querySelector<HTMLElement>(".flex-1.overflow-y-auto")!;

beforeEach(() => {
  window.localStorage.clear();
  cap.params = null;
  cap.total = 874; // 9 pages at QUEUE_PAGE_SIZE = 100
  cap.emails = makeEmails(5);
});

describe("pagination — filter change resets to page 1", () => {
  it("goes back to offset 0 when a filter changes while on a later page", async () => {
    const user = userEvent.setup();
    renderQueue();

    // Move to page 2.
    await user.click(screen.getByRole("button", { name: "Page 2" }));
    expect(cap.params?.offset).toBe(QUEUE_PAGE_SIZE);

    // Change the lane filter → a new result set → page resets to 1 (offset 0),
    // with the new filter applied.
    await user.click(screen.getByRole("button", { name: "FAQ" }));
    expect(cap.params?.offset).toBe(0);
    expect(cap.params?.lane).toBe("faq");
  });
});

describe("pagination — each page remembers its own scroll", () => {
  it("opens a new page at the top and restores scroll when returning", async () => {
    const user = userEvent.setup();
    renderQueue();

    const list = listContainer();
    installScrollTop(list);

    // Scroll down on page 1 and let the restoration hook record it.
    list.scrollTop = 250;
    fireEvent.scroll(list);

    // Page 2 is a fresh view → starts at the top (its key has no saved scroll).
    await user.click(screen.getByRole("button", { name: "Page 2" }));
    expect(list.scrollTop).toBe(0);

    // Back to page 1 → its own scroll position is restored.
    await user.click(screen.getByRole("button", { name: "Page 1" }));
    expect(list.scrollTop).toBe(250);
  });
});
