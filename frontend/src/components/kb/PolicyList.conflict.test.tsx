import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { PolicyList } from "./PolicyList";
import type { ConflictReport, PolicyDocument } from "@/types";

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
  render(
    <PolicyList
      policies={[policy]}
      onRetire={vi.fn()}
      onReactivate={vi.fn()}
      onRecheck={onRecheck}
      pendingKey={null}
      recheckingKey={null}
    />
  );
  return onRecheck;
}

describe("PolicyList conflict strip (2e)", () => {
  it("shows a conflict count that expands to the detail + snippet", async () => {
    const user = userEvent.setup();
    renderList(makePolicy({ conflict_report: REPORT_WITH_CONFLICT }));

    const toggle = screen.getByRole("button", { name: /1 conflict/i });
    await user.click(toggle);

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
    const { container: c1 } = render(
      <PolicyList policies={[makePolicy({ conflict_report: null })]}
        onRetire={vi.fn()} onReactivate={vi.fn()} onRecheck={vi.fn()}
        pendingKey={null} recheckingKey={null} />
    );
    expect(within(c1 as HTMLElement).queryByRole("button", { name: /re-check/i })).toBeNull();
  });
});
