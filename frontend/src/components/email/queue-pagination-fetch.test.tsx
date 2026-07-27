/**
 * Clicking a page number fetches that page — i.e. it drives the queue query's
 * `offset` (offset = (page - 1) * QUEUE_PAGE_SIZE), captured off the real
 * useEmailQueue call the workspace makes.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
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

beforeEach(() => {
  window.localStorage.clear();
  cap.params = null;
  cap.total = 874; // 9 pages at QUEUE_PAGE_SIZE=100
  cap.emails = makeEmails(3);
});

describe("queue pagination → fetch", () => {
  it("requests offset 0 on the first page", () => {
    renderQueue();
    expect(cap.params?.offset).toBe(0);
    expect(cap.params?.limit).toBe(QUEUE_PAGE_SIZE);
  });

  it("fetches the right offset when a page number is clicked", async () => {
    const user = userEvent.setup();
    renderQueue();

    await user.click(screen.getByRole("button", { name: "Page 2" }));
    expect(cap.params?.offset).toBe(QUEUE_PAGE_SIZE); // page 2 → offset 100

    await user.click(screen.getByRole("button", { name: "Page 3" }));
    expect(cap.params?.offset).toBe(2 * QUEUE_PAGE_SIZE); // page 3 → offset 200
  });

  it("steps offset with the next chevron", async () => {
    const user = userEvent.setup();
    renderQueue();

    await user.click(screen.getByRole("button", { name: "Next page" }));
    expect(cap.params?.offset).toBe(QUEUE_PAGE_SIZE);
  });
});
