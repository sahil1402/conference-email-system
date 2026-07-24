/**
 * "Set status, no reply" (mark new / open / solved without a reply) over the REAL
 * TicketPage + EmailWorkspace + EmailDetail wiring. Mirrors approve-send-chain's
 * post-C4 harness: selection is URL-driven (the ticket route), useSetEmailStatus
 * runs for real, only the network call (`setEmailStatus`), the ticket-detail
 * fetch, and router navigation are stubbed. Advancing after success is a
 * NAVIGATION to the neighbouring ticket, so those tests assert router.push.
 */
import { describe, it, expect, beforeAll, beforeEach, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import TicketPage from "@/app/tickets/[ticketId]/page";
import type { Email } from "@/types";

const state = vi.hoisted(() => ({
  emails: [] as unknown[],
  current: null as unknown,
  setStatus: vi.fn(),
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: state.push }),
}));

// API boundary: keep the real module, override only the status write.
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, setEmailStatus: state.setStatus };
});

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
  state.setStatus.mockReset();
  state.push.mockReset();
  state.setStatus.mockResolvedValue({ status: "SOLVED", send: { state: "status_set_no_reply" } });
});

describe("set status, no reply (on the ticket route)", () => {
  it("primary button marks solved, then advances to the neighbour", async () => {
    const user = userEvent.setup();
    renderTicket();
    await waitForDetail();

    await user.click(screen.getByRole("button", { name: /mark solved · no reply/i }));

    await waitFor(() => expect(state.setStatus).toHaveBeenCalledWith(1, "solved"));
    await waitFor(() => expect(state.push).toHaveBeenCalledWith("/tickets/22001"));
  });

  it("dropdown offers new / open / solved, each firing its status", async () => {
    const user = userEvent.setup();
    renderTicket();
    await waitForDetail();

    await user.click(
      screen.getByRole("button", { name: /set another status without replying/i })
    );
    await user.click(await screen.findByRole("menuitem", { name: /mark as open/i }));

    await waitFor(() => expect(state.setStatus).toHaveBeenCalledWith(1, "open"));
  });

  it("Ctrl+Alt+X marks the ticket solved without a reply", async () => {
    renderTicket();
    await waitForDetail();

    // Matched on e.code, so the synthetic event must carry code "KeyX".
    fireEvent.keyDown(window, { code: "KeyX", key: "x", ctrlKey: true, altKey: true });

    await waitFor(() => expect(state.setStatus).toHaveBeenCalledWith(1, "solved"));
  });

  it("Ctrl+Alt+X is ignored while typing in the draft", async () => {
    renderTicket();
    await waitForDetail();

    const textarea = screen.getByRole("textbox", { name: /draft/i });
    // Event originates in the textarea → the typing guard must swallow it.
    fireEvent.keyDown(textarea, { code: "KeyX", key: "x", ctrlKey: true, altKey: true });

    await new Promise((r) => setTimeout(r, 0));
    expect(state.setStatus).not.toHaveBeenCalled();
  });

  it("a failed status change stays on the ticket (no advance)", async () => {
    state.setStatus.mockRejectedValue({ detail: "Zendesk write failed", status: 502 });
    const user = userEvent.setup();
    renderTicket();
    await waitForDetail();

    await user.click(screen.getByRole("button", { name: /mark solved · no reply/i }));

    await waitFor(() => expect(state.setStatus).toHaveBeenCalledTimes(1));
    await new Promise((r) => setTimeout(r, 0));
    expect(state.push).not.toHaveBeenCalled(); // stayed put on failure
  });
});
