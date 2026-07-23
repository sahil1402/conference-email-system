/**
 * Route test for /tickets/[ticketId] (Pieces C2 / C2b / C3 loading).
 *
 * Renders the REAL shared EmailWorkspace with the REAL useEmailByTicket hook —
 * only the network boundary (getEmailByTicketId) and the ambient data hooks are
 * stubbed. Covers the happy path, the loading state, and that the audit trail
 * keeps its EmailAuditTrailEntry shape. The not-found / error STATES are covered
 * in page.error-states.test.tsx (which mocks the hook to drive error branches
 * deterministically, without react-query surfacing an expected query rejection
 * as an unhandled rejection).
 */
import { describe, it, expect, beforeAll, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import type { Email, EmailDetailResponse } from "@/types";
import TicketPage from "./page";

const state = vi.hoisted(() => ({ getByTicket: vi.fn(), push: vi.fn() }));

// Row clicks + advance navigate via useRouter.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: state.push }),
}));

// API boundary: keep the real module, override only the single-email fetch.
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, getEmailByTicketId: state.getByTicket };
});

// Ambient hooks the shared workspace uses → static values (not under test here).
// A second row (#22001) so a "click a different row" navigation can be asserted.
vi.mock("@/hooks/useEmailQueue", () => ({
  useEmailQueue: () => ({
    emails: [EMAIL, OTHER_EMAIL],
    total: 2,
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
vi.mock("@/hooks/useEmailQueueStream", () => ({
  useEmailQueueStream: () => ({ status: "live" }),
}));
vi.mock("@/hooks/useChairs", () => ({
  useChairs: () => ({ chairs: [], byId: new Map(), isLoading: false, isError: false }),
}));
vi.mock("@/hooks/useAppConfig", () => ({
  useAppConfig: () => ({ allowAutoSend: false }),
}));
vi.mock("@/hooks/useEmailThread", () => ({
  useEmailThread: () => ({ messages: [], isLoading: false, isError: false }),
}));

const EMAIL = {
  id: 7,
  sender: "requester@univ.edu",
  sender_name: "Requester",
  subject: "How do I update my paper?",
  body: "body",
  status: "DRAFT_GENERATED",
  routing: { lane: "human_review" },
  draft: {
    draft_text: "Here is the grounded answer.",
    citations: [],
    notes_for_chair: "",
    history: [],
  },
  classification: { intent: "submission_deadline", confidence: 0.9 },
  assigned_chair_id: null,
  zendesk_ticket_id: 21567,
  zendesk_ticket_url: null,
  received_at: "2026-01-01T12:00:00Z",
  created_at: "2026-01-01T12:00:00Z",
  updated_at: "2026-01-01T12:00:00Z",
  redrafting: false,
} as unknown as Email;

// A second list row with a different ticket id, for the row-click nav test.
const OTHER_EMAIL = {
  ...EMAIL,
  id: 8,
  subject: "A different ticket",
  zendesk_ticket_id: 22001,
} as unknown as Email;

// audit_trail uses EmailAuditTrailEntry's shape (`timestamp`/`metadata`, string
// email_id) — NOT AuditEntry's `created_at`/`details`/number email_id.
const RESPONSE: EmailDetailResponse = {
  email: EMAIL,
  audit_trail: [
    {
      id: 101,
      email_id: "7",
      action: "classified",
      actor: "pipeline",
      timestamp: "2026-01-02T10:00:00Z",
      metadata: { intent: "submission_deadline" },
    },
  ],
};

beforeAll(() => {
  // Radix DropdownMenu (SplitActionButton, inside EmailDetail) needs these.
  window.HTMLElement.prototype.hasPointerCapture = vi.fn();
  window.HTMLElement.prototype.releasePointerCapture = vi.fn();
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
});

function renderPage(ticketId = "21567") {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <TicketPage params={{ ticketId }} />
    </QueryClientProvider>
  );
}

describe("TicketPage (/tickets/[ticketId])", () => {
  beforeEach(() => {
    state.getByTicket.mockReset();
    state.push.mockReset();
  });

  it("clicking a different list row navigates to that row's ticket URL", async () => {
    state.getByTicket.mockResolvedValue(RESPONSE);
    const user = userEvent.setup();

    renderPage("21567");
    await screen.findByRole("heading", { name: "How do I update my paper?" });

    // Click the OTHER row (#22001) in the list pane.
    await user.click(
      screen.getByRole("button", { name: /a different ticket/i })
    );
    // Navigates by the row's zendesk_ticket_id, not its DB id (8).
    expect(state.push).toHaveBeenCalledWith("/tickets/22001");
  });

  it("renders the shared workspace's full layout (filter sidebar + drag-resize + list)", async () => {
    state.getByTicket.mockResolvedValue(RESPONSE);

    renderPage("21567");
    await screen.findByRole("heading", { name: "How do I update my paper?" });

    expect(
      screen.getByRole("button", { name: "Hide filters" })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("separator", { name: "Resize the email list" })
    ).toBeInTheDocument();
  });

  it("the filter sidebar collapse toggle is functional", async () => {
    state.getByTicket.mockResolvedValue(RESPONSE);
    const user = userEvent.setup();

    renderPage("21567");
    await screen.findByRole("heading", { name: "How do I update my paper?" });

    const toggle = screen.getByRole("button", { name: "Hide filters" });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    await user.click(toggle);
    expect(
      screen.getByRole("button", { name: "Show filters" })
    ).toHaveAttribute("aria-expanded", "false");
  });

  it("resolves the detail email via the dedicated by-ticket fetch", async () => {
    state.getByTicket.mockResolvedValue(RESPONSE);

    renderPage("21567");

    expect(
      await screen.findByRole("heading", { name: "How do I update my paper?" })
    ).toBeInTheDocument();
    expect(screen.getByText("#21567")).toBeInTheDocument();
    expect(state.getByTicket).toHaveBeenCalledWith("21567");
  });

  it("passes the audit trail through with EmailAuditTrailEntry shape (not coerced to AuditEntry)", async () => {
    state.getByTicket.mockResolvedValue(RESPONSE);

    renderPage("21567");

    const trail = await screen.findByTestId("ticket-audit-trail");
    expect(trail).toHaveTextContent("classified");
    expect(trail).toHaveTextContent("pipeline");
    expect(trail).toHaveTextContent(/2026/);
    expect(screen.getByText(/Activity \(1\)/)).toBeInTheDocument();
  });

  it("shows a loading indicator while the fetch is pending (C3)", async () => {
    // A deferred fetch keeps the query pending long enough to assert the loading
    // UI, then we settle it so no pending query outlives the test.
    let resolveFetch: (v: EmailDetailResponse) => void = () => {};
    state.getByTicket.mockReturnValue(
      new Promise<EmailDetailResponse>((res) => {
        resolveFetch = res;
      })
    );

    renderPage("21567");

    // The app's LoadingSpinner (role=status, aria-label "Loading").
    expect(screen.getByLabelText("Loading")).toBeInTheDocument();

    resolveFetch(RESPONSE);
    await screen.findByRole("heading", { name: "How do I update my paper?" });
  });
});
