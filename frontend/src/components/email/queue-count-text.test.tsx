/**
 * The "Showing X–Y of Z tickets" count now lives at the bottom of the filter
 * sidebar (below the Zendesk status section), not atop the list, and reflects
 * the backend's filtered `total`.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import QueuePage from "@/app/queue/page";
import type { Email } from "@/types";

const q = vi.hoisted(() => ({ emails: [] as Email[], total: 0 }));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/hooks/useEmailQueue", () => ({
  useEmailQueue: () => ({
    emails: q.emails,
    total: q.total,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
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

/** The filter sidebar column (queried by the stable collapse attribute). */
const filterColumn = () => document.querySelector<HTMLElement>("[data-collapsed]")!;

beforeEach(() => {
  window.localStorage.clear();
  q.emails = [];
  q.total = 0;
});

describe("queue count text (relocated to the filter sidebar)", () => {
  it("shows the range + filtered total when a page is a subset", () => {
    q.emails = makeEmails(3);
    q.total = 874;
    renderQueue();

    // In the filter column, not atop the list.
    expect(
      within(filterColumn()).getByText("Showing 1–3 of 874 tickets")
    ).toBeInTheDocument();
  });

  it("shows just the count when the whole set fits on one page", () => {
    q.emails = makeEmails(3);
    q.total = 3;
    renderQueue();

    expect(within(filterColumn()).getByText("3 tickets")).toBeInTheDocument();
  });

  it("singularizes a single ticket", () => {
    q.emails = makeEmails(1);
    q.total = 1;
    renderQueue();

    expect(within(filterColumn()).getByText("1 ticket")).toBeInTheDocument();
  });

  it("says 'No tickets' when the filtered set is empty", () => {
    q.emails = [];
    q.total = 0;
    renderQueue();

    expect(within(filterColumn()).getByText("No tickets")).toBeInTheDocument();
  });
});

/**
 * The count text is centered and pinned to the BOTTOM of the filter column.
 *
 * Asserted on classes deliberately: the pin is a three-link chain spanning two
 * components (column `flex-col` → panel `flex-1` → footer `mt-auto`), and jsdom
 * computes no layout, so nothing else can catch a broken link. Unlike an
 * incidental spacing class, these ARE the mechanism.
 */
describe("count text placement (centered, bottom-anchored)", () => {
  it("centers the text and anchors its block to the bottom of the column", () => {
    q.emails = makeEmails(3);
    q.total = 874;
    renderQueue();

    const text = within(filterColumn()).getByText("Showing 1–3 of 874 tickets");
    expect(text).toHaveClass("text-center");

    // The footer block absorbs the column's free space.
    const footer = text.parentElement!;
    expect(footer).toHaveClass("mt-auto");

    // …which only works if its ancestors form the flex chain.
    const panel = footer.parentElement!;
    expect(panel.className).toMatch(/\bflex\b/);
    expect(panel.className).toMatch(/\bflex-col\b/);
    expect(panel.className).toMatch(/\bflex-1\b/);

    const column = filterColumn();
    expect(column.className).toMatch(/\bflex\b/);
    expect(column.className).toMatch(/\bflex-col\b/);
  });

  it("keeps the pagination controls in the same block, below the text", () => {
    q.emails = makeEmails(3);
    q.total = 874;
    renderQueue();

    const text = within(filterColumn()).getByText("Showing 1–3 of 874 tickets");
    const footer = text.parentElement!;
    const nav = within(footer).getByRole("navigation", { name: "Pagination" });

    // Same flow block, nav after the text — no overlap, spacing owned by the
    // block's own space-y rather than by the pin.
    expect(footer).toContainElement(nav);
    expect(
      text.compareDocumentPosition(nav) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();
  });
});
