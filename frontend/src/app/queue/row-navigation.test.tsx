/**
 * Queue row navigation (Piece C4).
 *
 * Selection on /queue is now URL-driven: clicking a row navigates to
 * /tickets/{zendesk_ticket_id} (a shareable URL), and /queue itself holds no
 * in-memory selection — its detail pane shows the "select an email" empty state.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import type { Email } from "@/types";
import QueuePage from "./page";

const state = vi.hoisted(() => ({ push: vi.fn(), emails: [] as unknown[] }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: state.push }),
}));
vi.mock("@/hooks/useEmailQueue", () => ({
  useEmailQueue: () => ({
    emails: state.emails,
    total: state.emails.length,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
}));
vi.mock("@/hooks/useQueueFacets", () => ({
  useQueueFacets: () => ({
    byZendeskStatus: {},
    bySource: {},
    sources: [],
    isLoading: false,
    isError: false,
  }),
}));
vi.mock("@/hooks/useChairs", () => ({
  useChairs: () => ({ chairs: [], byId: new Map(), isLoading: false, isError: false }),
}));
vi.mock("@/hooks/useAppConfig", () => ({
  useAppConfig: () => ({ allowAutoSend: false }),
}));
vi.mock("@/hooks/useEmailQueueStream", () => ({
  useEmailQueueStream: () => ({ status: "live" }),
}));

function makeEmail(overrides: Partial<Email> = {}): Email {
  return {
    id: 1,
    sender: "author@university.edu",
    sender_name: "Author",
    subject: "Deadline question",
    body: "When is the deadline?",
    status: "DRAFT_GENERATED",
    received_at: "2026-07-20T09:00:00Z",
    assigned_chair_id: null,
    source: "zendesk",
    zendesk_ticket_id: 21567,
    zendesk_status: "open",
    classification: { intent: "deadline_extension", confidence: 0.9 } as never,
    routing: { lane: "human_review" } as never,
    draft: { draft_text: "Dear Author…" } as never,
    created_at: "2026-07-20T09:00:00Z",
    updated_at: "2026-07-20T09:00:00Z",
    ...overrides,
  } as Email;
}

function renderQueue() {
  // EmailWorkspace's action hooks call useQueryClient (no action fires here).
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <QueuePage />
    </QueryClientProvider>
  );
}

describe("queue row navigation (C4)", () => {
  beforeEach(() => {
    state.push.mockReset();
    state.emails = [
      makeEmail({ id: 1, subject: "Deadline question", zendesk_ticket_id: 21567 }),
      makeEmail({ id: 2, subject: "Travel grant", zendesk_ticket_id: 22001 }),
    ];
  });

  it("navigates to /tickets/{zendesk_ticket_id} (not the DB id) on row click", async () => {
    const user = userEvent.setup();
    renderQueue();

    await user.click(screen.getByRole("button", { name: /deadline question/i }));

    // Ticket id 21567 — NOT the DB id (1).
    expect(state.push).toHaveBeenCalledWith("/tickets/21567");
    expect(state.push).not.toHaveBeenCalledWith("/tickets/1");
  });

  it("navigates using each row's own ticket id", async () => {
    const user = userEvent.setup();
    renderQueue();

    await user.click(screen.getByRole("button", { name: /travel grant/i }));

    expect(state.push).toHaveBeenCalledWith("/tickets/22001");
  });

  it("renders the empty 'select an email' state (no in-memory selection)", () => {
    renderQueue();

    // The detail pane shows the workspace's default empty state; no email is
    // selected on /queue, and clicking (above) navigates rather than selecting.
    expect(screen.getByText("Select an email to review")).toBeInTheDocument();
  });

  it("does NOT auto-redirect on load — /queue stays an overview (C6 decision)", () => {
    // Decision (C6): /queue's landing state is the empty detail pane, NOT a
    // redirect to the first ticket. Loading /queue with rows present must not
    // navigate anywhere.
    renderQueue();

    expect(state.push).not.toHaveBeenCalled();
    expect(screen.getByText("Select an email to review")).toBeInTheDocument();
    // The list still renders its rows (overview intact).
    expect(
      screen.getByRole("button", { name: /deadline question/i })
    ).toBeInTheDocument();
  });
});
