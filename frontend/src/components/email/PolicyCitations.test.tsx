/**
 * Policy Citations panel — the three-branch selector in EmailDetail.
 *
 * Branch 1 (retrieved_chunks)  → rich CitationCard   ← live as of 2a
 * Branch 2 (draft.citations)   → badge pills          ← fallback, unchanged
 * Branch 3 (neither)           → empty state          ← fallback, unchanged
 *
 * Before 2a the API never populated `retrieved_chunks`, so branch 1 was
 * unreachable in practice and every ticket fell through to 2 or 3. These tests
 * pin that branch 1 now wins when the data is present, that it renders the 2a
 * payload shape (id/title/content/category, NO `score`), and that the two
 * fallbacks are untouched.
 *
 * Renders the REAL ticket route (TicketPage → EmailWorkspace → EmailDetail),
 * the same harness as EmailDetail.test.tsx.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import TicketPage from "@/app/tickets/[ticketId]/page";
import type { Email, RetrievedChunk } from "@/types";

const state = vi.hoisted(() => ({ current: null as unknown, push: vi.fn() }));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: state.push }) }));

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
    emails: [], total: 0, isLoading: false, isError: false, refetch: vi.fn(),
  }),
}));
vi.mock("@/hooks/useQueueFacets", () => ({
  useQueueFacets: () => ({
    byZendeskStatus: {}, bySource: {}, sources: [], isLoading: false, isError: false,
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

/** Exactly the shape backend `_hydrate_retrieved_chunks` serves — no `score`. */
const HYDRATED: RetrievedChunk[] = [
  {
    policy_id: "policy_186",
    title: "AAAI-27 Paper Modification Guidelines",
    content: "After the July 31 deadline nothing can be changed.",
    category: "submission",
  },
  {
    policy_id: "policy_172",
    title: "Abstract and Paper Submission (part 2)",
    content: "Abstracts must be registered before the paper deadline.",
    category: "submission",
  },
];

function makeEmail(overrides: Partial<Email> = {}): Email {
  return {
    id: 1,
    sender: "author@university.edu",
    sender_name: "Author",
    subject: "Withdrawal request",
    body: "Please withdraw my paper.",
    status: "DRAFT_GENERATED",
    received_at: "2026-07-20T09:00:00Z",
    assigned_chair_id: null,
    source: "zendesk",
    zendesk_ticket_id: 22980,
    zendesk_status: "new",
    classification: { intent: "submission_upload_help", confidence: 0.76 } as never,
    routing: { lane: "human_review", rationale: "needs review" } as never,
    draft: { draft_text: "Dear Author, …", citations: [] } as never,
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
      <TicketPage
        params={{ ticketId: String((state.current as Email).zendesk_ticket_id) }}
      />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  state.push.mockReset();
});

describe("Policy Citations — branch 1 (hydrated retrieved_chunks)", () => {
  it("renders rich citation cards from the 2a payload", async () => {
    state.current = makeEmail({ retrieved_chunks: HYDRATED });
    renderTicket();

    // Title + content of each chunk are visible (the rich card, not a pill).
    expect(
      await screen.findByText("AAAI-27 Paper Modification Guidelines")
    ).toBeInTheDocument();
    expect(
      screen.getByText("After the July 31 deadline nothing can be changed.")
    ).toBeInTheDocument();
    expect(
      screen.getByText("Abstract and Paper Submission (part 2)")
    ).toBeInTheDocument();

    // Each card is a button labelled for the modal it opens.
    expect(
      screen.getByRole("button", { name: "View policy AAAI-27 Paper Modification Guidelines" })
    ).toBeInTheDocument();

    // Branch 3 must NOT appear — this is what regressed before 2a.
    expect(
      screen.queryByText("No policy citations for this email.")
    ).not.toBeInTheDocument();
  });

  it("renders without a `score` field (2a omits it — nothing may depend on it)", async () => {
    state.current = makeEmail({ retrieved_chunks: HYDRATED });
    renderTicket();

    expect(await screen.findByText("AAAI-27 Paper Modification Guidelines")).toBeInTheDocument();
    // No chunk carries a score; the card must still show the category badge.
    expect(HYDRATED.every((c) => c.score === undefined)).toBe(true);
    expect(screen.getAllByText("submission").length).toBeGreaterThan(0);
  });

  it("wins over draft.citations when BOTH are present", async () => {
    state.current = makeEmail({
      retrieved_chunks: HYDRATED,
      draft: { draft_text: "…", citations: ["policy_999"] } as never,
    });
    renderTicket();

    expect(await screen.findByText("AAAI-27 Paper Modification Guidelines")).toBeInTheDocument();
    // The branch-2 pill for policy_999 must not render.
    expect(screen.queryByText("policy_999")).not.toBeInTheDocument();
  });

  it("renders EVERY chunk the API returns — no client-side cap", async () => {
    // INVERTED from its original form, which asserted `chunks.slice(0, 3)`.
    // That cap predated manual invoke and silently hid the chair's forced
    // policy (always the 4th entry). The ranked set is bounded server-side by
    // MAX_RETRIEVED_CHUNKS and at most one policy can be forced, so trusting
    // the API's list is correct — a second cap here only loses data.
    const many: RetrievedChunk[] = [1, 2, 3, 4, 5].map((n) => ({
      policy_id: `policy_${n}`,
      title: `Policy Number ${n}`,
      content: `Body ${n}`,
      category: "submission",
    }));
    state.current = makeEmail({ retrieved_chunks: many });
    renderTicket();

    expect(await screen.findByText("Policy Number 1")).toBeInTheDocument();
    expect(screen.getByText("Policy Number 3")).toBeInTheDocument();
    expect(screen.getByText("Policy Number 4")).toBeInTheDocument();
    expect(screen.getByText("Policy Number 5")).toBeInTheDocument();
  });
});

describe("Policy Citations — fallback branches (must be unaffected by 2a)", () => {
  it("branch 2: falls back to draft.citations pills when chunks are absent", async () => {
    state.current = makeEmail({
      retrieved_chunks: null,
      draft: { draft_text: "…", citations: ["policy_186", "policy_171"] } as never,
    });
    renderTicket();

    expect(
      await screen.findByText("Policies cited in the draft (click for full text):")
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "View policy policy_186" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "View policy policy_171" })).toBeInTheDocument();
    expect(
      screen.queryByText("No policy citations for this email.")
    ).not.toBeInTheDocument();
  });

  it("branch 3: empty state for a NULL-context email (neither source)", async () => {
    state.current = makeEmail({
      retrieved_chunks: null,
      draft: { draft_text: "…", citations: [] } as never,
    });
    renderTicket();

    expect(
      await screen.findByText("No policy citations for this email.")
    ).toBeInTheDocument();
  });

  it("branch 3: an EMPTY chunks array still falls through to the fallbacks", async () => {
    // 2a serves [] for a real query that matched nothing (distinct from null).
    state.current = makeEmail({
      retrieved_chunks: [],
      draft: { draft_text: "…", citations: [] } as never,
    });
    renderTicket();

    expect(
      await screen.findByText("No policy citations for this email.")
    ).toBeInTheDocument();
  });
});

describe("Policy Citations — chair-forced chunk (manual invoke)", () => {
  const RANKED_PLUS_FORCED: RetrievedChunk[] = [
    ...HYDRATED,
    {
      policy_id: "policy_171",
      title: "Abstract and Paper Submission",
      content: "Every submission requires both an abstract and a paper.",
      category: "submission",
    },
    {
      policy_id: "int_paper-deletion__v2",
      title: "Paper Deletion",
      content: "Withdrawal requires written confirmation.",
      category: "deletion",
    },
  ];

  it("renders the FORCED 4th chunk instead of silently dropping it", async () => {
    // Regression: the panel used to be `chunks.slice(0, 3)`. With
    // MAX_RETRIEVED_CHUNKS=3 the chair's forced policy is always the 4th entry,
    // so it was invisible every single time — a success banner with no change.
    state.current = makeEmail({
      retrieved_chunks: RANKED_PLUS_FORCED,
      retrieval_context: { forced_policy_key: "int_paper-deletion__v2" },
    });
    renderTicket();

    // All four render — the three ranked AND the forced one.
    expect(await screen.findByText("Paper Deletion")).toBeInTheDocument();
    expect(screen.getByText("AAAI-27 Paper Modification Guidelines")).toBeInTheDocument();
    expect(screen.getByText("Abstract and Paper Submission (part 2)")).toBeInTheDocument();
    expect(screen.getByText("Abstract and Paper Submission")).toBeInTheDocument();
    expect(
      screen.getByText("Withdrawal requires written confirmation.")
    ).toBeInTheDocument();
  });

  it("marks ONLY the forced chunk as chair-added", async () => {
    state.current = makeEmail({
      retrieved_chunks: RANKED_PLUS_FORCED,
      retrieval_context: { forced_policy_key: "int_paper-deletion__v2" },
    });
    renderTicket();

    await screen.findByText("Paper Deletion");
    // Exactly one badge, on exactly the forced card.
    expect(screen.getAllByText("Added by you")).toHaveLength(1);
    expect(
      screen.getByRole("button", { name: "View policy Paper Deletion (added by you)" })
    ).toBeInTheDocument();
    // A retrieved card keeps its plain label.
    expect(
      screen.getByRole("button", { name: "View policy AAAI-27 Paper Modification Guidelines" })
    ).toBeInTheDocument();
  });

  it("shows no marker when nothing was forced (normal draft)", async () => {
    state.current = makeEmail({
      retrieved_chunks: HYDRATED,
      retrieval_context: { forced_policy_key: null },
    });
    renderTicket();

    await screen.findByText("AAAI-27 Paper Modification Guidelines");
    expect(screen.queryByText("Added by you")).not.toBeInTheDocument();
  });

  it("shows no marker when retrieval_context is absent entirely (legacy row)", async () => {
    state.current = makeEmail({ retrieved_chunks: HYDRATED });
    renderTicket();

    await screen.findByText("AAAI-27 Paper Modification Guidelines");
    expect(screen.queryByText("Added by you")).not.toBeInTheDocument();
  });

  it("marks the forced card even when it was ALSO in the ranked set", async () => {
    // Task 3 skips duplicate injection, so a forced key can name a ranked chunk;
    // there is no 4th card then, but it must still be marked as chair-added.
    state.current = makeEmail({
      retrieved_chunks: HYDRATED,
      retrieval_context: { forced_policy_key: "policy_172" },
    });
    renderTicket();

    await screen.findByText("Abstract and Paper Submission (part 2)");
    expect(screen.getAllByText("Added by you")).toHaveLength(1);
  });
});
