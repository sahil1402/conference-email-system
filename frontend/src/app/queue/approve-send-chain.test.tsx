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

  it("5. approve ok + send fails → error banner, approve NOT rolled back", async () => {
    state.send.mockRejectedValue({ detail: "Zendesk write failed", status: 502 });
    const user = userEvent.setup();
    renderQueue();
    await selectEmail(user, "Deadline question");

    await user.click(screen.getByRole("button", { name: "Submit as Solved" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/approved locally/i);
    expect(alert).toHaveTextContent(/not sent/i);
    expect(alert).toHaveTextContent(/Zendesk write failed/i);
    // Approve fired exactly once and was never re-called / compensated.
    expect(state.approve).toHaveBeenCalledTimes(1);
  });

  it("6. retry re-sends the same payload and clears the banner on success", async () => {
    state.send
      .mockRejectedValueOnce({ detail: "Zendesk write failed", status: 502 })
      .mockResolvedValueOnce({ status: "sent", send: { state: "sent" } });
    const user = userEvent.setup();
    renderQueue();
    await selectEmail(user, "Deadline question");

    await user.click(screen.getByRole("button", { name: "Submit as Solved" }));
    const alert = await screen.findByRole("alert");

    await user.click(within(alert).getByRole("button", { name: /retry/i }));

    // Re-sent with the SAME payload; approve was NOT called again.
    await waitFor(() => expect(state.send).toHaveBeenCalledTimes(2));
    expect(state.send.mock.calls[0]).toEqual(state.send.mock.calls[1]);
    expect(state.approve).toHaveBeenCalledTimes(1);
    // Banner clears once the retry succeeds.
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });

  it("7. the error banner is scoped to the affected email only", async () => {
    state.emails = [
      makeEmail({ id: 1, subject: "Deadline question" }),
      makeEmail({ id: 2, subject: "Travel grant" }),
    ];
    state.send.mockRejectedValue({ detail: "Zendesk write failed", status: 502 });
    const user = userEvent.setup();
    renderQueue();

    await selectEmail(user, "Deadline question");
    await user.click(screen.getByRole("button", { name: "Submit as Solved" }));
    await screen.findByRole("alert"); // banner shows for email 1

    // Switch to a different email → the failure banner must not follow.
    await selectEmail(user, "Travel grant");
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
