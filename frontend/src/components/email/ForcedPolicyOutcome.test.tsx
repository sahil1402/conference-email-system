/**
 * Manual-invoke outcome banner (5d) — the UI reaction to `forced_policy_applied`.
 *
 * The field is PERSISTED on the email, so the banner is gated on local session
 * state (the chair approved in the popover just now). These tests pin all three
 * tri-states AND the "don't re-announce an old invoke" rule, which is the whole
 * reason the gate exists.
 *
 * Renders the real ticket route (TicketPage → EmailWorkspace → EmailDetail), the
 * same harness as the other EmailDetail tests.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import TicketPage from "@/app/tickets/[ticketId]/page";
import type { Email, PolicyDocument } from "@/types";

const state = vi.hoisted(() => ({
  current: null as unknown,
  list: vi.fn(),
  retry: vi.fn(),
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: state.push }) }));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, listPolicies: state.list, retryEmail: state.retry };
});

vi.mock("@/hooks/useEmailByTicket", () => ({
  useEmailByTicket: () => ({
    email: state.current, auditTrail: [], isLoading: false, isError: false,
    error: null, refetch: vi.fn(),
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
vi.mock("@/hooks/useAppConfig", () => ({ useAppConfig: () => ({ allowAutoSend: false }) }));
vi.mock("@/hooks/useEmailQueueStream", () => ({
  useEmailQueueStream: () => ({ status: "live" }),
}));
vi.mock("@/hooks/useEmailThread", () => ({
  useEmailThread: () => ({ messages: [], isLoading: false, isError: false }),
}));

const POLICY: PolicyDocument = {
  policy_key: "int_paper-deletion__v2", title: "Paper Deletion",
  content: "Withdrawal requires written confirmation.", category: "deletion",
  visibility: "internal", status: "active", source: null, updated_at: null,
  supersedes: null, superseded_by: null, root_key: null, version: 1,
} as PolicyDocument;

function makeEmail(over: Partial<Email> = {}): Email {
  return {
    id: 2662, sender: "a@b.com", sender_name: "Author",
    subject: "Withdrawal request", body: "Please withdraw.",
    status: "DRAFT_GENERATED", received_at: "2026-07-20T09:00:00Z",
    assigned_chair_id: null, source: "zendesk", zendesk_ticket_id: 22980,
    zendesk_status: "new",
    classification: { intent: "submission_upload_help", confidence: 0.76 } as never,
    routing: { lane: "human_review", rationale: "x" } as never,
    draft: { draft_text: "Dear Author, …", citations: [] } as never,
    created_at: "2026-07-20T09:00:00Z", updated_at: "2026-07-20T09:00:00Z",
    ...over,
  } as Email;
}

function renderTicket() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <TicketPage params={{ ticketId: "22980" }} />
    </QueryClientProvider>
  );
}

const SUCCESS = /was added to this draft's grounding/;
const WARNING = /couldn't be applied to this draft/;

/** Drive the popover through search → detail → Approve. */
async function forcePolicy(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: /Add policy/ }));
  const input = screen.getByRole("combobox", { name: "Search policies" });
  await user.type(input, "deletion");
  await user.click(await screen.findByText("Paper Deletion"));
  await user.click(screen.getByRole("button", { name: "Approve" }));
  await waitFor(() => expect(state.retry).toHaveBeenCalled());
}

beforeEach(() => {
  state.push.mockReset();
  state.list.mockReset();
  state.list.mockResolvedValue({ policies: [POLICY] });
  state.retry.mockReset();
  state.retry.mockResolvedValue({ email_id: "2662", redrafting: true });
});

describe("forced-policy outcome banner", () => {
  it("shows nothing before the chair forces anything (null case)", async () => {
    state.current = makeEmail({ forced_policy_applied: null });
    renderTicket();

    expect(await screen.findByRole("button", { name: /Add policy/ })).toBeInTheDocument();
    expect(screen.queryByText(SUCCESS)).not.toBeInTheDocument();
    expect(screen.queryByText(WARNING)).not.toBeInTheDocument();
  });

  it("a NORMAL redraft (forced_policy_applied null) shows no indicator", async () => {
    // Even with the field explicitly null and a settled ticket, nothing renders —
    // the majority path must be completely unaffected by this feature.
    state.current = makeEmail({ forced_policy_applied: null, redrafting: false });
    renderTicket();
    await screen.findByRole("button", { name: /Add policy/ });

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText(SUCCESS)).not.toBeInTheDocument();
    expect(screen.queryByText(WARNING)).not.toBeInTheDocument();
  });

  it("true → success indicator naming the policy", async () => {
    const user = userEvent.setup();
    state.current = makeEmail({ forced_policy_applied: true, redrafting: false });
    renderTicket();

    await forcePolicy(user);

    expect(await screen.findByText(SUCCESS)).toBeInTheDocument();
    expect(screen.getByText("int_paper-deletion__v2")).toBeInTheDocument();
    expect(screen.queryByText(WARNING)).not.toBeInTheDocument();
  });

  it("false → a visible warning, clearly distinct from success", async () => {
    const user = userEvent.setup();
    state.current = makeEmail({ forced_policy_applied: false, redrafting: false });
    renderTicket();

    await forcePolicy(user);

    const warning = await screen.findByText(WARNING);
    expect(warning).toBeInTheDocument();
    expect(screen.queryByText(SUCCESS)).not.toBeInTheDocument();
    // Announced to assistive tech and visually marked as a problem, not a success.
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("stays silent while the re-draft is still in flight", async () => {
    const user = userEvent.setup();
    // redrafting=true ⇒ the outcome isn't known yet, so neither banner may show.
    state.current = makeEmail({ forced_policy_applied: false, redrafting: true });
    renderTicket();

    await forcePolicy(user);

    expect(screen.queryByText(SUCCESS)).not.toBeInTheDocument();
    expect(screen.queryByText(WARNING)).not.toBeInTheDocument();
  });

  it("does NOT re-announce a past invoke when an old ticket is opened", async () => {
    // The persisted field says a forced policy applied at some point, but this
    // session did nothing — the chair must not see a stale confirmation.
    state.current = makeEmail({ forced_policy_applied: true, redrafting: false });
    renderTicket();
    await screen.findByRole("button", { name: /Add policy/ });

    expect(screen.queryByText(SUCCESS)).not.toBeInTheDocument();
    expect(screen.queryByText(WARNING)).not.toBeInTheDocument();
  });

  it("survives re-renders without flipping or duplicating", async () => {
    const user = userEvent.setup();
    state.current = makeEmail({ forced_policy_applied: true, redrafting: false });
    const { rerender } = renderTicket();

    await forcePolicy(user);
    expect(await screen.findByText(SUCCESS)).toBeInTheDocument();

    // A poll-driven re-render with identical data must not duplicate the banner.
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <TicketPage params={{ ticketId: "22980" }} />
      </QueryClientProvider>
    );
    expect(screen.getAllByText(SUCCESS)).toHaveLength(1);
  });
});
