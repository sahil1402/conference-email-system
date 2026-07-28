import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { PolicyList } from "./PolicyList";
import type { PolicyDocument } from "@/types";

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

function renderList(policies: PolicyDocument[]) {
  const onRetire = vi.fn();
  const onReactivate = vi.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <PolicyList
        policies={policies}
        onRetire={onRetire}
        onReactivate={onReactivate}
        onRecheck={vi.fn()}
        pendingKey={null}
        recheckingKey={null}
      />
    </QueryClientProvider>
  );
  return { onRetire, onReactivate };
}

/** The stuck state: reactivating a policy whose successor had been retired used
 *  to leave superseded_by set, so the row read as "active" while every edit was
 *  rejected by the backend's active-AND-current guard. */
describe("PolicyList — active but superseded", () => {
  it("renders a 'superseded' badge instead of the green 'active' one", () => {
    renderList([makePolicy({ status: "active", superseded_by: "int_a__v2" })]);

    expect(screen.getByText("superseded")).toBeInTheDocument();
    expect(screen.queryByText("active")).toBeNull();
  });

  it("disables Edit and explains why, inline and on hover", () => {
    renderList([makePolicy({ status: "active", superseded_by: "int_a__v2" })]);

    const edit = screen.getByRole("button", { name: "Edit" });
    expect(edit).toBeDisabled();
    expect(edit).toHaveAttribute("title", expect.stringMatching(/replaced by a newer version/i));

    // The reason is visible without hovering, not only in the tooltip.
    const note = screen.getByText(/replaced by a newer version/i);
    expect(note).toBeInTheDocument();
    // …and is announced with the disabled button rather than leaving a screen
    // reader with a bare dimmed control.
    expect(edit).toHaveAttribute("aria-describedby", note.id);
    expect(note.id).toBeTruthy();
  });

  it("still allows retiring a superseded row", () => {
    renderList([makePolicy({ status: "active", superseded_by: "int_a__v2" })]);

    expect(screen.getByRole("button", { name: "Retire" })).toBeEnabled();
  });

  it("names the successor when it is present in the current view", () => {
    renderList([
      makePolicy({ policy_key: "int_a", superseded_by: "int_a__v2" }),
      makePolicy({
        policy_key: "int_a__v2",
        title: "Reviewer response deadline (revised)",
        status: "inactive",
        supersedes: "int_a",
        root_key: "int_a",
        version: 2,
      }),
    ]);

    const supersededRow = screen.getAllByRole("listitem")[0];
    expect(
      within(supersededRow).getByText(/Reviewer response deadline \(revised\)/)
    ).toBeInTheDocument();
  });

  it("falls back to the successor's key when it is filtered out of the view", () => {
    renderList([makePolicy({ superseded_by: "int_a__v2" })]);

    expect(screen.getByText(/int_a__v2/)).toBeInTheDocument();
  });
});

describe("PolicyList — states that must be unaffected", () => {
  it("leaves a normal active policy alone", () => {
    renderList([makePolicy({ status: "active", superseded_by: null })]);

    expect(screen.getByText("active")).toBeInTheDocument();
    expect(screen.queryByText("superseded")).toBeNull();
    expect(screen.getByRole("button", { name: "Edit" })).toBeEnabled();
    expect(screen.queryByText(/replaced by a newer version/i)).toBeNull();
  });

  it("shows a retired base as plain 'inactive' with Reactivate, NOT superseded", () => {
    // Boundary case: an inactive row carrying superseded_by is the NORMAL
    // post-edit state of an edited base — every lineage has one. It must keep
    // rendering as a plain retirement; treating it as the stuck state would
    // mislabel routine history and hide the Reactivate action.
    renderList([
      makePolicy({
        status: "inactive",
        superseded_by: "int_a__v2",
        version: 1,
      }),
    ]);

    expect(screen.getByText("inactive")).toBeInTheDocument();
    expect(screen.queryByText("superseded")).toBeNull();
    expect(screen.queryByText(/replaced by a newer version/i)).toBeNull();

    expect(screen.getByRole("button", { name: "Reactivate" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Retire" })).toBeNull();
  });
});
