/**
 * Route test for /tickets/[ticketId] (Piece C2) — standalone rendering.
 *
 * Faithful to production: useEmailByTicket runs for real through a real
 * QueryClient; only the network boundary (getEmailByTicketId) is a spy at the
 * @/lib/api layer, mirroring how approve-send-chain.test.tsx stubs the boundary.
 * The queue list hook + the ambient data hooks (chairs/config) are stubbed to
 * static values so the page renders deterministically without real fetches.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import type { Email, EmailDetailResponse } from "@/types";
import TicketPage from "./page";

const state = vi.hoisted(() => ({ getByTicket: vi.fn() }));

// API boundary: keep the real module, override only the single-email fetch.
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, getEmailByTicketId: state.getByTicket };
});

// Ambient hooks → static values (not the fetch under test).
vi.mock("@/hooks/useEmailQueue", () => ({
  useEmailQueue: () => ({
    emails: [],
    total: 0,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
}));
vi.mock("@/hooks/useChairs", () => ({
  useChairs: () => ({ chairs: [], byId: new Map(), isLoading: false, isError: false }),
}));
vi.mock("@/hooks/useAppConfig", () => ({
  useAppConfig: () => ({ allowAutoSend: false }),
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

// audit_trail uses EmailAuditTrailEntry's shape: `timestamp` + `metadata` +
// string email_id — NOT AuditEntry's `created_at`/`details`/number email_id.
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
  beforeEach(() => state.getByTicket.mockReset());

  it("fetches by ticket id and renders EmailDetail with that email's data", async () => {
    state.getByTicket.mockResolvedValue(RESPONSE);

    renderPage("21567");

    // Detail pane shows the fetched email (subject + ticket number).
    expect(
      await screen.findByRole("heading", { name: "How do I update my paper?" })
    ).toBeInTheDocument();
    expect(screen.getByText("#21567")).toBeInTheDocument();

    // The dedicated by-ticket fetch was used (not a queue-array lookup).
    expect(state.getByTicket).toHaveBeenCalledWith("21567");
  });

  it("passes the audit trail through with EmailAuditTrailEntry shape (not coerced to AuditEntry)", async () => {
    state.getByTicket.mockResolvedValue(RESPONSE);

    renderPage("21567");

    const trail = await screen.findByTestId("ticket-audit-trail");
    // `action` + `actor` render...
    expect(trail).toHaveTextContent("classified");
    expect(trail).toHaveTextContent("pipeline");
    // ...and the entry was read via `timestamp` (EmailAuditTrailEntry). If the
    // page had coerced to AuditEntry (which uses `created_at`, absent here), no
    // date would render. The formatted 2026 date proves `timestamp` was read.
    expect(trail).toHaveTextContent(/2026/);
    expect(screen.getByText(/Activity \(1\)/)).toBeInTheDocument();
  });
});
