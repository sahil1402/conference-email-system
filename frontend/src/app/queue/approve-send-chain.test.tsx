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
import { act, render, screen, waitFor, within } from "@testing-library/react";
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

/** The three submit statuses and the visibility each one is FIXED to (AAAI
 *  2026-08). This table is the test-side mirror of REPLY_PUBLIC_BY_STATUS in
 *  EmailDetail.tsx — deliberately written out by hand rather than imported, so
 *  a change to the source map fails these tests instead of silently agreeing
 *  with itself. */
const VISIBILITY_BY_STATUS = [
  { label: "Pending", target: "pending", isPublic: false },
  { label: "Open", target: "open", isPublic: false },
  { label: "Solved", target: "solved", isPublic: true },
] as const;

/** Pick a submit status from the split button's dropdown and wait for the
 *  primary button's label to follow it (which also persists the selection —
 *  what the Ctrl+Alt+S path reads). */
async function selectStatus(
  user: ReturnType<typeof userEvent.setup>,
  label: string
) {
  await user.click(
    screen.getByRole("button", { name: /choose a resulting status/i })
  );
  await user.click(
    await screen.findByRole("menuitem", { name: new RegExp(`^${label}$`, "i") })
  );
  await screen.findByRole("button", { name: `Submit as ${label}` });
}

/** Ctrl+Alt+S on window (where EmailDetail registers the handler). Mirrors
 *  EmailDetail.test.tsx's helper: a native event with `code: "KeyS"` and
 *  getModifierState("AltGraph") === false, since the handler branches on both. */
function pressCtrlAltS() {
  const event = new KeyboardEvent("keydown", {
    key: "s",
    code: "KeyS",
    ctrlKey: true,
    altKey: true,
    bubbles: true,
    cancelable: true,
  });
  Object.defineProperty(event, "getModifierState", { value: () => false });
  act(() => {
    window.dispatchEvent(event);
  });
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
  // AAAI (2026-08): reply visibility is FIXED per submit status — Solved is
  // always a public reply to the requester, Pending and Open are always
  // internal notes. There is no chair-facing toggle any more, so `public` is
  // never a free variable: it is a pure function of the chosen status, and the
  // click path and the Ctrl+Alt+S path must agree on it (they read the status
  // from DIFFERENT variables — the onAction argument vs the persisted
  // approveStatus — so both are covered below).
  it.each(VISIBILITY_BY_STATUS)(
    "1. clicking Submit as $label always sends public: $isPublic",
    async ({ label, target, isPublic }) => {
      const user = userEvent.setup();
      renderTicket();
      await waitForDetail();

      await selectStatus(user, label);
      await user.click(screen.getByRole("button", { name: `Submit as ${label}` }));

      await waitFor(() =>
        expect(state.approve).toHaveBeenCalledWith(
          1,
          expect.objectContaining({ approved_by: "chair", target_status: target })
        )
      );
      await waitFor(() =>
        expect(state.send).toHaveBeenCalledWith(1, {
          public: isPublic,
          target_status: target,
        })
      );
    }
  );

  it.each(VISIBILITY_BY_STATUS)(
    "2. Ctrl+Alt+S submits $label with the same fixed public: $isPublic as a click",
    async ({ label, target, isPublic }) => {
      const user = userEvent.setup();
      renderTicket();
      await waitForDetail();

      await selectStatus(user, label);
      pressCtrlAltS();

      await waitFor(() =>
        expect(state.send).toHaveBeenCalledWith(1, {
          public: isPublic,
          target_status: target,
        })
      );
    }
  );

  it("3. Solved stays public even when the chair has edited the draft", async () => {
    // An edit changes the Zendesk TAG the backend applies (ai_drafted vs
    // ai_auto_replied) but must NOT change who sees the reply — visibility
    // follows the status alone.
    const user = userEvent.setup();
    renderTicket();
    await waitForDetail();

    const textarea = screen.getByDisplayValue(
      "Dear Author, the deadline is in the CFP."
    );
    await user.type(textarea, " Please see the CFP page.");
    await user.click(screen.getByRole("button", { name: "Submit as Solved" }));

    await waitFor(() =>
      expect(state.approve).toHaveBeenCalledWith(
        1,
        expect.objectContaining({
          final_text: "Dear Author, the deadline is in the CFP. Please see the CFP page.",
        })
      )
    );
    await waitFor(() =>
      expect(state.send).toHaveBeenCalledWith(1, {
        public: true,
        target_status: "solved",
      })
    );
  });

  it("3b. renders no reply-visibility control (the toggle is soft-removed)", async () => {
    // Guard against the commented-out SendVisibilityToggle being restored
    // without also reverting REPLY_PUBLIC_BY_STATUS — that combination would
    // render a switch whose value is silently ignored at submit time.
    renderTicket();
    await waitForDetail();

    expect(screen.queryByRole("switch")).toBeNull();
    expect(screen.queryByText(/send to requester/i)).toBeNull();
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

  it("5. optimistically advances even when the background send fails", async () => {
    state.send.mockRejectedValue({ detail: "Zendesk write failed", status: 502 });
    const user = userEvent.setup();
    renderTicket();
    await waitForDetail();

    await user.click(screen.getByRole("button", { name: "Submit as Solved" }));

    // Optimistic: we advance to the neighbour the instant approve lands and run
    // the send in the background, so a failing send does NOT block the advance —
    // it surfaces in the queue-level notice once on another ticket (see the
    // cross-navigation test). Approve fired once and was never rolled back.
    await waitFor(() => expect(state.push).toHaveBeenCalledWith("/tickets/22001"));
    expect(state.approve).toHaveBeenCalledTimes(1);
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

  it("8. a failed send stays surfaced after the chair opens another ticket (C6 cross-navigation)", async () => {
    state.send.mockRejectedValue({ detail: "Zendesk write failed", status: 502 });
    const user = userEvent.setup();

    // Same QueryClient across the "navigation" so the workspace's mutation +
    // failed-send state persists (as it does in-app: /tickets/[id] → /tickets/[id]
    // only changes the route param — the workspace component is not remounted).
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const ui = (ticketId: string) => (
      <QueryClientProvider client={qc}>
        <TicketPage params={{ ticketId }} />
      </QueryClientProvider>
    );
    const { rerender } = render(ui("21567"));
    await waitForDetail();

    await user.click(screen.getByRole("button", { name: "Submit as Solved" }));

    // Optimistic advance fired immediately (we don't wait on the send); the send
    // then fails in the background.
    await waitFor(() => expect(state.push).toHaveBeenCalledWith("/tickets/22001"));

    // Chair opens a DIFFERENT ticket (#22001). The workspace persists.
    state.current = makeEmail({ id: 2, subject: "Travel grant", zendesk_ticket_id: 22001 });
    rerender(ui("22001"));

    // The #21567 failure now surfaces via the queue-level notice on the new
    // ticket, with a link back to reopen + retry it — not lost on navigation.
    await waitFor(() => {
      const notice = screen.getByRole("alert");
      expect(notice).toHaveTextContent(/failed to send/i);
      expect(
        within(notice).getByRole("button", { name: /#21567/ })
      ).toBeInTheDocument();
    });
    // And the scoped "approved locally" banner is no longer shown (we're on #22001).
    expect(screen.queryByText(/approved locally/i)).toBeNull();
  });

  it("9. advances WITHOUT waiting for the send to resolve (optimistic)", async () => {
    // A send that never settles during the test: if the advance waited on the
    // round-trip, push would never fire. Optimism = we advance anyway, with the
    // send still in flight.
    state.send.mockReturnValue(new Promise(() => {}));
    const user = userEvent.setup();
    renderTicket();
    await waitForDetail();

    await user.click(screen.getByRole("button", { name: "Submit as Solved" }));

    await waitFor(() => expect(state.push).toHaveBeenCalledWith("/tickets/22001"));
    expect(state.send).toHaveBeenCalledTimes(1); // fired, still pending
  });
});
