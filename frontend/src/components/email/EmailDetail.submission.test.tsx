/**
 * SubmissionDetails wiring into EmailDetail's header (subtask 3).
 *
 * Renders the REAL ticket route (TicketPage → EmailWorkspace → EmailDetail),
 * the same harness as EmailDetail.chairnotes.test.tsx, so these assert the
 * panel as the app actually composes it — not the component in isolation,
 * which SubmissionDetails.test.tsx already covers.
 *
 * SCOPE LIMIT — jsdom performs NO layout, so nothing here proves the panel
 * LOOKS right beneath the Intent row. These pin DOM ORDER and presence:
 * Intent, then the panel, then the conversation. Visual confirmation needs a
 * human; there is no browser driver on this host.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import TicketPage from "@/app/tickets/[ticketId]/page";
import type { Email, ExtractionData } from "@/types";

const state = vi.hoisted(() => ({
  current: null as unknown,
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: state.push }),
}));

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
    emails: [],
    total: 0,
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

const FULL_EXTRACTION: ExtractionData = {
  submission_numbers: ["22336"],
  openreview_forum_ids: ["Ab3xY9kLm2"],
  authors: [
    {
      name: "Jane Roe",
      email: "jane@example.edu",
      affiliation: "Example University",
    },
  ],
  method: "llm_distiller",
};

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
    extraction: null,
    created_at: "2026-07-20T09:00:00Z",
    updated_at: "2026-07-20T09:00:00Z",
    ...overrides,
  } as Email;
}

function renderTicket(overrides: Partial<Email> = {}) {
  state.current = makeEmail(overrides);
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <TicketPage params={{ ticketId: "21567" }} />
    </QueryClientProvider>
  );
}

/** Wait for EmailDetail to be mounted (its submit control is present). */
async function waitForDetail() {
  await screen.findByRole("button", { name: "Submit as Solved" });
}

function panel(): HTMLElement | null {
  return screen.queryByRole("group", { name: /submission details/i });
}

beforeEach(() => {
  state.push.mockReset();
});

describe("EmailDetail — SubmissionDetails presence", () => {
  it("shows the panel when the email carries a full extraction", async () => {
    renderTicket({ extraction: FULL_EXTRACTION });
    await waitForDetail();

    expect(panel()).toBeInTheDocument();
    expect(screen.getByText("22336")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /openreview/i })
    ).toBeInTheDocument();
    expect(screen.getByText("Jane Roe")).toBeInTheDocument();
  });

  it("shows nothing extra when extraction is null", async () => {
    renderTicket({ extraction: null });
    await waitForDetail();

    expect(panel()).toBeNull();
    expect(screen.queryByText("Submission Details")).toBeNull();
  });

  it("shows nothing extra when the extraction found nothing", async () => {
    // Examined-but-empty: the panel must stay silent, not render a bare title.
    renderTicket({
      extraction: {
        submission_numbers: [],
        openreview_forum_ids: [],
        authors: [],
        method: "llm_distiller",
      },
    });
    await waitForDetail();

    expect(panel()).toBeNull();
  });

  it("needs no conditional wrapper at the call site", async () => {
    // The Intent row is the panel's neighbour; with nothing to show, the header
    // must look exactly as it did before this was wired in — no empty element
    // between Intent and the conversation.
    renderTicket({ extraction: null });
    await waitForDetail();

    const intentLabel = screen.getByText("Intent");
    const intentRow = intentLabel.parentElement!;
    expect(intentRow.nextElementSibling).toBeNull();
  });

  it("renders the panel even when the classification is absent", async () => {
    // The two blocks are independent: no Intent row must not suppress the panel.
    renderTicket({ classification: null, extraction: FULL_EXTRACTION });
    await waitForDetail();

    expect(screen.queryByText("Intent")).toBeNull();
    expect(panel()).toBeInTheDocument();
  });
});

describe("EmailDetail — SubmissionDetails placement", () => {
  it("sits after the Intent row and before the conversation", async () => {
    renderTicket({ extraction: FULL_EXTRACTION });
    await waitForDetail();

    const intentLabel = screen.getByText("Intent");
    const group = panel()!;

    // DOCUMENT_POSITION_FOLLOWING: the panel comes after the Intent row.
    expect(
      intentLabel.compareDocumentPosition(group) &
        Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();

    // ...and before the conversation region that follows the header.
    const conversation = document.querySelector(".conf-html") ?? group.closest("header")!.nextElementSibling!;
    expect(
      group.compareDocumentPosition(conversation) &
        Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();
  });

  it("is the Intent row's immediate next sibling", async () => {
    renderTicket({ extraction: FULL_EXTRACTION });
    await waitForDetail();

    const intentRow = screen.getByText("Intent").parentElement!;
    expect(intentRow.nextElementSibling).toBe(panel());
  });

  it("lives inside the header, not the scroll body", async () => {
    // Placement inside <header> is what gives it the same space-y-3 rhythm and
    // full width as the Intent row it sits under.
    renderTicket({ extraction: FULL_EXTRACTION });
    await waitForDetail();

    expect(panel()!.closest("header")).not.toBeNull();
  });

  it("shares the Intent row's surface shell classes", async () => {
    // SCOPE LIMIT: jsdom does no layout, so the shared look is pinned by class,
    // not measured — same padding and radius as the row directly above.
    renderTicket({ extraction: FULL_EXTRACTION });
    await waitForDetail();

    const intentRow = screen.getByText("Intent").parentElement!;
    for (const shell of ["rounded-lg", "px-3", "py-2"]) {
      expect(intentRow).toHaveClass(shell);
      expect(panel()).toHaveClass(shell);
    }
  });
});
