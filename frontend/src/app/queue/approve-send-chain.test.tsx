/**
 * Approve-then-send chain (Piece B-impl-3a/3b) — integration test over the REAL
 * QueuePage + EmailDetail wiring.
 *
 * Faithful to production: the action hooks (useApproveEmail/useSendEmail) run for
 * real through a real QueryClient; only the network boundary is stubbed —
 * `approveEmail`/`sendEmail` are spies injected at the @/lib/api layer (NOT by
 * mocking the hooks). The surrounding data-fetch hooks are stubbed to static
 * values so the queue renders a selectable email deterministically.
 */
import { describe, it, expect, beforeAll, beforeEach, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import QueuePage from "./page";
import type { Email } from "@/types";

// Shared, mutable across the mock factories (which are hoisted above imports).
// The spies live here so tests reconfigure them without re-mocking the module.
const state = vi.hoisted(() => ({
  emails: [] as unknown[],
  approve: vi.fn(),
  send: vi.fn(),
}));

// API boundary: keep the real module, override only the two write calls.
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, approveEmail: state.approve, sendEmail: state.send };
});

// Data-fetch hooks → static values (not action hooks; the chain stays real).
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
// Rendered inside EmailDetail (ConversationThread) — stub so it needs no network.
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

function renderQueue() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <QueuePage />
    </QueryClientProvider>
  );
}

// Select an email from the list by its subject (the list item is a button).
async function selectEmail(user: ReturnType<typeof userEvent.setup>, subject: string) {
  await user.click(screen.getByRole("button", { name: new RegExp(subject, "i") }));
  // Detail pane action button confirms selection.
  await screen.findByRole("button", { name: "Submit as Solved" });
}

beforeAll(() => {
  // Radix DropdownMenu (used by SplitActionButton) needs these in jsdom.
  window.HTMLElement.prototype.hasPointerCapture = vi.fn();
  window.HTMLElement.prototype.releasePointerCapture = vi.fn();
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
  // Minimal ResizeObserver stub for Radix's popper (jsdom lacks it).
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

beforeEach(() => {
  // Reset persisted submit-status / visibility between cases (best-effort —
  // this jsdom's localStorage stub omits clear()).
  window.localStorage?.clear?.();
  state.emails = [makeEmail()];
  state.approve.mockReset();
  state.send.mockReset();
  state.approve.mockResolvedValue(makeEmail({ status: "approved" }));
  state.send.mockResolvedValue({ status: "sent", send: { state: "sent" } });
});

describe("approve → send chain", () => {
  it("1. approves then sends with status from the selector + toggle value", async () => {
    const user = userEvent.setup();
    renderQueue();
    await selectEmail(user, "Deadline question");

    // Pick a resulting status from the split-button dropdown.
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

  it("2. defaults to an internal note (public: false) with no interaction", async () => {
    const user = userEvent.setup();
    renderQueue();
    await selectEmail(user, "Deadline question");

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
    renderQueue();
    await selectEmail(user, "Deadline question");

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
    renderQueue();
    await selectEmail(user, "Deadline question");

    await user.click(screen.getByRole("button", { name: "Submit as Solved" }));

    await waitFor(() => expect(state.approve).toHaveBeenCalledTimes(1));
    // Give any (incorrect) chained send a chance to fire, then assert it didn't.
    await new Promise((r) => setTimeout(r, 0));
    expect(state.send).not.toHaveBeenCalled();
  });

  it("5. approve ok + background send fails → queue-level notice, approve NOT rolled back", async () => {
    state.send.mockRejectedValue({ detail: "Zendesk write failed", status: 502 });
    const user = userEvent.setup();
    renderQueue();
    await selectEmail(user, "Deadline question");

    await user.click(screen.getByRole("button", { name: "Submit as Solved" }));

    // Optimistic resolve advanced us off the ticket, so the failure surfaces in
    // the queue-level notice (not the selection-scoped banner), naming the
    // ticket so the chair can reopen it.
    const notice = await screen.findByRole("alert");
    expect(notice).toHaveTextContent(/failed to send/i);
    expect(notice).toHaveTextContent(/stays in the queue/i);
    expect(
      within(notice).getByRole("button", { name: /#21567/ })
    ).toBeInTheDocument();
    // Approve fired exactly once and was never re-called / compensated.
    expect(state.approve).toHaveBeenCalledTimes(1);
  });

  it("6. reopening a failed ticket from the notice retries with the same payload", async () => {
    state.send
      .mockRejectedValueOnce({ detail: "Zendesk write failed", status: 502 })
      .mockResolvedValueOnce({ status: "sent", send: { state: "sent" } });
    const user = userEvent.setup();
    renderQueue();
    await selectEmail(user, "Deadline question");

    await user.click(screen.getByRole("button", { name: "Submit as Solved" }));

    // Reopen the failed ticket from the queue-level notice.
    const notice = await screen.findByRole("alert");
    await user.click(within(notice).getByRole("button", { name: /#21567/ }));

    // The selection-scoped banner (with Retry) now shows in the detail pane.
    // Scope to the banner — EmailDetail also has a pipeline "Retry" button.
    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent(/approved locally/i);
    await user.click(within(banner).getByRole("button", { name: /retry/i }));

    // Re-sent with the SAME payload; approve was NOT called again.
    await waitFor(() => expect(state.send).toHaveBeenCalledTimes(2));
    expect(state.send.mock.calls[0]).toEqual(state.send.mock.calls[1]);
    expect(state.approve).toHaveBeenCalledTimes(1);
  });

  it("7. a background send failure shows a queue-level notice, not a scoped banner on the advanced-to email", async () => {
    state.emails = [
      makeEmail({ id: 1, subject: "Deadline question", zendesk_ticket_id: 21567 }),
      makeEmail({ id: 2, subject: "Travel grant", zendesk_ticket_id: 22001 }),
    ];
    state.send.mockRejectedValue({ detail: "Zendesk write failed", status: 502 });
    const user = userEvent.setup();
    renderQueue();

    await selectEmail(user, "Deadline question");
    await user.click(screen.getByRole("button", { name: "Submit as Solved" }));

    // Optimistically advanced to the next ticket…
    expect(
      await screen.findByRole("heading", { name: /travel grant/i })
    ).toBeInTheDocument();
    // …and the failure surfaces in the queue-level notice naming the failed
    // ticket (#21567), NOT as a scoped "approved locally" banner on email 2.
    const notice = await screen.findByRole("alert");
    expect(
      within(notice).getByRole("button", { name: /#21567/ })
    ).toBeInTheDocument();
    expect(screen.queryByText(/approved locally/i)).toBeNull();
  });

  it("8. advances to the next ticket after a successful send", async () => {
    state.emails = [
      makeEmail({ id: 1, subject: "Deadline question" }),
      makeEmail({ id: 2, subject: "Travel grant" }),
    ];
    const user = userEvent.setup();
    renderQueue();
    await selectEmail(user, "Deadline question");
    // Detail pane shows the first email (subject is an <h2> heading; list rows
    // are buttons, so a heading query targets the detail pane specifically).
    expect(
      screen.getByRole("heading", { name: /deadline question/i })
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Submit as Solved" }));

    // After approve→send resolve, selection advances to the next row (email 2).
    expect(
      await screen.findByRole("heading", { name: /travel grant/i })
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: /deadline question/i })
    ).toBeNull();
  });

  it("9. advances to the previous ticket when the last one is sent", async () => {
    state.emails = [
      makeEmail({ id: 1, subject: "Deadline question" }),
      makeEmail({ id: 2, subject: "Travel grant" }),
    ];
    const user = userEvent.setup();
    renderQueue();
    await selectEmail(user, "Travel grant"); // the LAST row
    expect(
      screen.getByRole("heading", { name: /travel grant/i })
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Submit as Solved" }));

    // No next row → fall back to the previous one (email 1).
    expect(
      await screen.findByRole("heading", { name: /deadline question/i })
    ).toBeInTheDocument();
  });
});
