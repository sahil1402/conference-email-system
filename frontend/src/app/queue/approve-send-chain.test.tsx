/**
 * Approve → send → advance chain (Pieces B-impl-3 + C4).
 *
 * Since C4 made selection URL-driven, the review/approve interaction lives on
 * the ticket route (/tickets/[id]), not the queue. This renders the REAL
 * TicketPage + EmailWorkspace: the action hooks (useApproveEmail/useSendEmail)
 * run for real through a real QueryClient; only the network boundary
 * (approveEmail/sendEmail), the ticket-detail fetch (useEmailByTicket), and
 * router navigation are stubbed. Advancing after a successful send is now a
 * NAVIGATION to the neighbouring ticket, so those tests assert router.push.
 */
import { describe, it, expect, beforeAll, beforeEach, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import TicketPage from "@/app/tickets/[ticketId]/page";
import type { Email } from "@/types";

const state = vi.hoisted(() => ({
  emails: [] as unknown[],
  current: null as unknown,
  approve: vi.fn(),
  send: vi.fn(),
  push: vi.fn(),
}));

// Router: capture navigations (advance + row clicks).
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: state.push }),
}));

// API boundary: keep the real module, override only the two write calls.
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, approveEmail: state.approve, sendEmail: state.send };
});

// The ticket detail is the "selected" email — driven directly.
vi.mock("@/hooks/useEmailByTicket", () => ({
  useEmailByTicket: () => ({
    email: state.current,
    auditTrail: [],
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

// The list (for the advance neighbour + the row-click nav test).
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
vi.mock("@/hooks/useEmailThread", () => ({
  useEmailThread: () => ({ messages: [], isLoading: false, isError: false }),
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
    routing: { lane: "human_review", rationale: "needs review" } as never,
    draft: { draft_text: "Dear Author, the deadline is in the CFP." } as never,
    created_at: "2026-07-20T09:00:00Z",
    updated_at: "2026-07-20T09:00:00Z",
    ...overrides,
  } as Email;
}

function renderTicket() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <TicketPage params={{ ticketId: String((state.current as Email).zendesk_ticket_id) }} />
    </QueryClientProvider>
  );
}

// The ticket email is already "selected"; wait for the detail's submit control.
async function waitForDetail() {
  await screen.findByRole("button", { name: "Submit as Solved" });
}

beforeAll(() => {
  window.HTMLElement.prototype.hasPointerCapture = vi.fn();
  window.HTMLElement.prototype.releasePointerCapture = vi.fn();
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

beforeEach(() => {
  window.localStorage?.clear?.();
  // Current ticket (id 1, #21567) + a neighbour (id 2, #22001) to advance to.
  state.current = makeEmail();
  state.emails = [
    makeEmail(),
    makeEmail({ id: 2, subject: "Travel grant", zendesk_ticket_id: 22001 }),
  ];
  state.approve.mockReset();
  state.send.mockReset();
  state.push.mockReset();
  state.approve.mockResolvedValue(makeEmail({ status: "approved" }));
  state.send.mockResolvedValue({ status: "sent", send: { state: "sent" } });
});

describe("approve → send chain (on the ticket route)", () => {
  it("1. approves then sends with the status from the selector + toggle value", async () => {
    const user = userEvent.setup();
    renderTicket();
    await waitForDetail();

    await user.click(screen.getByRole("button", { name: /choose a resulting status/i }));
    await user.click(await screen.findByRole("menuitem", { name: /solved/i }));
    await user.click(screen.getByRole("button", { name: /submit as solved/i }));

    await waitFor(() =>
      expect(state.approve).toHaveBeenCalledWith(
        1,
        expect.objectContaining({ approved_by: "chair", target_status: "solved" })
      )
    );
    await waitFor(() =>
      expect(state.send).toHaveBeenCalledWith(1, {
        public: false,
        target_status: "solved",
      })
    );
  });

  it("2. defaults to an internal note (public: false)", async () => {
    const user = userEvent.setup();
    renderTicket();
    await waitForDetail();

    await user.click(screen.getByRole("button", { name: "Submit as Solved" }));

    await waitFor(() =>
      expect(state.send).toHaveBeenCalledWith(1, {
        public: false,
        target_status: "solved",
      })
    );
  });

  it("3. toggling 'Send to requester' sends with public: true", async () => {
    const user = userEvent.setup();
    renderTicket();
    await waitForDetail();

    await user.click(screen.getByRole("switch")); // internal → public
    await user.click(screen.getByRole("button", { name: "Submit as Solved" }));

    await waitFor(() =>
      expect(state.send).toHaveBeenCalledWith(1, {
        public: true,
        target_status: "solved",
      })
    );
  });

  it("4. does NOT send when approve fails", async () => {
    state.approve.mockRejectedValue({ detail: "approve blew up", status: 400 });
    const user = userEvent.setup();
    renderTicket();
    await waitForDetail();

    await user.click(screen.getByRole("button", { name: "Submit as Solved" }));

    await waitFor(() => expect(state.approve).toHaveBeenCalledTimes(1));
    await new Promise((r) => setTimeout(r, 0));
    expect(state.send).not.toHaveBeenCalled();
    expect(state.push).not.toHaveBeenCalled(); // no advance on a failed approve
  });

  it("5. send fails → stay on the ticket with the scoped banner, approve NOT rolled back, no advance", async () => {
    state.send.mockRejectedValue({ detail: "Zendesk write failed", status: 502 });
    const user = userEvent.setup();
    renderTicket();
    await waitForDetail();

    await user.click(screen.getByRole("button", { name: "Submit as Solved" }));

    // Scoped "approved locally, retry the send" banner shows on this ticket.
    const banner = await screen.findByText(/approved locally/i);
    expect(banner).toBeInTheDocument();
    // Approve fired once, was not compensated, and we did NOT navigate away.
    expect(state.approve).toHaveBeenCalledTimes(1);
    expect(state.push).not.toHaveBeenCalled();
  });

  it("6. retrying the send from the banner re-sends the same payload, then advances", async () => {
    state.send
      .mockRejectedValueOnce({ detail: "Zendesk write failed", status: 502 })
      .mockResolvedValueOnce({ status: "sent", send: { state: "sent" } });
    const user = userEvent.setup();
    renderTicket();
    await waitForDetail();

    await user.click(screen.getByRole("button", { name: "Submit as Solved" }));

    // The scoped banner (with Retry) appears. Scope to it — EmailDetail also has
    // a pipeline "Retry" button.
    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent(/approved locally/i);
    await user.click(within(banner).getByRole("button", { name: /retry/i }));

    // Re-sent with the SAME payload; approve was NOT called again.
    await waitFor(() => expect(state.send).toHaveBeenCalledTimes(2));
    expect(state.send.mock.calls[0]).toEqual(state.send.mock.calls[1]);
    expect(state.approve).toHaveBeenCalledTimes(1);
    // On the successful retry, advance by navigating to the neighbour (#22001).
    await waitFor(() =>
      expect(state.push).toHaveBeenCalledWith("/tickets/22001")
    );
  });

  it("7. advances by navigating to the neighbouring ticket after a successful send", async () => {
    const user = userEvent.setup();
    renderTicket();
    await waitForDetail();

    await user.click(screen.getByRole("button", { name: "Submit as Solved" }));

    // #21567 sent → advance to the neighbour #22001 via the URL, NOT the DB id.
    await waitFor(() =>
      expect(state.push).toHaveBeenCalledWith("/tickets/22001")
    );
  });
});
