import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { PolicyList } from "./PolicyList";
import type { ConflictReport, PolicyDocument } from "@/types";

// Keep the API real except getPolicy, which ConflictEditRow calls to load the
// conflicting policy's full text for the highlighted edit reference.
const state = vi.hoisted(() => ({ getPolicy: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getPolicy: state.getPolicy,
}));

function makePolicy(overrides: Partial<PolicyDocument> = {}): PolicyDocument {
  return {
    policy_key: "int_a",
    title: "Reviewer response deadline",
    content: "Reviews are due within 10 days.",
    category: null,
    visibility: "internal",
    status: "active",
    source: "chair:1",
    updated_at: "2026-07-23T00:00:00Z",
    supersedes: null,
    superseded_by: null,
    root_key: null,
    version: 1,
    ...overrides,
  };
}

const REPORT_WITH_CONFLICT: ConflictReport = {
  checked_at: new Date().toISOString(),
  available: true,
  summary: "1 of 2 related policies conflict.",
  candidates_checked: ["policy_b", "policy_c"],
  conflicts: [
    {
      policy_key: "policy_b",
      title: "Reviewer deadline",
      explanation: "new says 10 days; this says 14",
      snippets: ["due within 14 days"],
    },
  ],
};

function renderList(policy: PolicyDocument, onRecheck = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <PolicyList
        policies={[policy]}
        onRetire={vi.fn()}
        onReactivate={vi.fn()}
        onRecheck={onRecheck}
        pendingKey={null}
        recheckingKey={null}
      />
    </QueryClientProvider>
  );
  return onRecheck;
}

beforeEach(() => {
  state.getPolicy.mockReset();
});

describe("PolicyList conflict strip (2e)", () => {
  it("shows a conflict count that expands to the detail + snippet", async () => {
    const user = userEvent.setup();
    renderList(makePolicy({ conflict_report: REPORT_WITH_CONFLICT }));

    await user.click(screen.getByRole("button", { name: /1 conflict/i }));

    expect(screen.getByText(/new says 10 days/i)).toBeInTheDocument();
    expect(screen.getByText(/policy_b/)).toBeInTheDocument();
    expect(screen.getByText(/due within 14 days/)).toBeInTheDocument();
  });

  it("fires onRecheck with the policy key from the strip", async () => {
    const user = userEvent.setup();
    const onRecheck = renderList(makePolicy({ conflict_report: REPORT_WITH_CONFLICT }));

    await user.click(screen.getByRole("button", { name: /1 conflict/i }));
    await user.click(screen.getByRole("button", { name: /re-check/i }));

    expect(onRecheck).toHaveBeenCalledWith("int_a");
  });

  it("highlights the conflicting passage when editing a conflicting policy", async () => {
    state.getPolicy.mockResolvedValue({
      policy_key: "policy_b",
      title: "Reviewer deadline",
      content: "Reviews are due within 14 days of assignment.",
      category: null,
      source: null,
      score: null,
    });
    const user = userEvent.setup();
    renderList(makePolicy({ conflict_report: REPORT_WITH_CONFLICT }));

    await user.click(screen.getByRole("button", { name: /1 conflict/i }));
    // Two "Edit" buttons exist (the owner row + the conflict item); the conflict
    // one is rendered last.
    const edits = screen.getAllByRole("button", { name: "Edit" });
    await user.click(edits[edits.length - 1]);

    // The read-only reference renders once the target's text loads.
    await screen.findByText(/conflicting passage/i);
    expect(state.getPolicy).toHaveBeenCalledWith("policy_b");
    const marks = document.querySelectorAll("mark");
    expect(marks.length).toBe(1);
    expect(marks[0].textContent).toBe("due within 14 days");
  });

  it("shows a 'no conflicts' line when the report is clean", () => {
    const clean: ConflictReport = {
      checked_at: new Date().toISOString(),
      available: true,
      summary: "No conflicts found among 2 related policies.",
      candidates_checked: ["policy_b", "policy_c"],
      conflicts: [],
    };
    renderList(makePolicy({ conflict_report: clean }));
    expect(screen.getByText(/no conflicts/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /re-check/i })).toBeInTheDocument();
  });

  it("renders nothing conflict-related when never checked or unavailable", () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { container } = render(
      <QueryClientProvider client={qc}>
        <PolicyList
          policies={[makePolicy({ conflict_report: null })]}
          onRetire={vi.fn()}
          onReactivate={vi.fn()}
          onRecheck={vi.fn()}
          pendingKey={null}
          recheckingKey={null}
        />
      </QueryClientProvider>
    );
    expect(within(container).queryByRole("button", { name: /re-check/i })).toBeNull();
  });
});
