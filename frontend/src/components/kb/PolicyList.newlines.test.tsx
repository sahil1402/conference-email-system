import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { PolicyList } from "./PolicyList";
import type { PolicyDocument } from "@/types";

/**
 * Mirrors int_program-commitee-roles-and-structure: a lead-in line followed by
 * bullets separated by a SINGLE "\n" — no blank lines anywhere. That shape is
 * the point of these tests. `white-space: normal` (the CSS default, and what
 * this card rendered with before the fix) collapses those newlines into spaces
 * and turns the body into one run-on paragraph, and any blank-line-dependent
 * approach (markdown paragraphs) would not fix it either.
 */
const BULLET_CONTENT = [
  "Nomenclature varies between conferences. At AAAI, we have three levels of seniority:",
  "- Program Committee member (PC/Reviewer). These are the reviewers.",
  "- Senior Program Committee Member (SPC): They supervise papers.",
  "- Area Chair (AC): They oversee larger numbers of papers.",
].join("\n");

function makePolicy(overrides: Partial<PolicyDocument> = {}): PolicyDocument {
  return {
    policy_key: "int_program-commitee-roles-and-structure",
    title: "Program committee roles and structure",
    content: BULLET_CONTENT,
    category: "roles",
    visibility: "internal",
    status: "active",
    source: "chair:1",
    updated_at: "2026-07-29T00:00:00Z",
    supersedes: null,
    superseded_by: null,
    root_key: null,
    version: 1,
    ...overrides,
  };
}

function renderList(policies: PolicyDocument[] = [makePolicy()]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <PolicyList
        policies={policies}
        onRetire={vi.fn()}
        onReactivate={vi.fn()}
        onRecheck={vi.fn()}
        pendingKey={null}
        recheckingKey={null}
      />
    </QueryClientProvider>
  );
}

/** The body <p> — matched on its first line, which it owns as a direct text node. */
function bodyEl(): HTMLElement {
  return screen.getByText(/Nomenclature varies between conferences/);
}

describe("PolicyList — policy body preserves source newlines", () => {
  it("renders the body with white-space: pre-wrap", () => {
    renderList();

    expect(bodyEl()).toHaveStyle({ whiteSpace: "pre-wrap" });
  });

  it("keeps the literal newlines in the rendered text, not collapsed to spaces", () => {
    renderList();

    // textContent, deliberately NOT toHaveTextContent: RTL normalises
    // whitespace, so the newlines would be invisible to that matcher.
    //
    // SCOPE LIMIT: this proves the DATA survives the render — nothing splits or
    // mangles the newlines on the way to the DOM. It does NOT prove the fix.
    // jsdom performs no layout, so textContent is identical with or without
    // pre-wrap (verified: this test still passes when the style is removed).
    // The `toHaveStyle` assertions above are the only ones that catch the bug.
    const text = bodyEl().textContent ?? "";
    expect(text).toContain("\n- Program Committee member");
    expect(text).toContain("\n- Senior Program Committee Member");
    expect(text).toContain("\n- Area Chair");
    expect(text).not.toContain("seniority: - Program Committee member");
  });

  it("applies pre-wrap in BOTH the collapsed and expanded states", async () => {
    // Guards the two render branches independently: the clamp is toggled on the
    // same element, so a future edit could easily restore the newline-eating
    // default in one state while the other still looks right.
    const user = userEvent.setup();
    renderList();

    expect(bodyEl()).toHaveStyle({ whiteSpace: "pre-wrap" });

    await user.click(screen.getByRole("button", { name: /show more/i }));

    expect(bodyEl()).toHaveStyle({ whiteSpace: "pre-wrap" });
  });
});

describe("PolicyList — truncation behaviour is unchanged by the newline fix", () => {
  it("clamps to two lines while collapsed and drops the clamp once expanded", async () => {
    // The paired half of the guard above: pre-wrap and line-clamp-2 live on the
    // same element, so this pins the clamp so it cannot be silently dropped
    // while "fixing" newlines (or re-added on the expanded branch).
    const user = userEvent.setup();
    renderList();

    expect(bodyEl()).toHaveClass("line-clamp-2");

    const toggle = screen.getByRole("button", { name: /show more/i });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);

    expect(bodyEl()).not.toHaveClass("line-clamp-2");
    expect(
      screen.getByRole("button", { name: /show less/i })
    ).toHaveAttribute("aria-expanded", "true");
  });

  it("keeps the full text in the DOM while collapsed (the clamp is visual only)", () => {
    renderList();

    // Nothing is truncated server- or render-side — so the clamped preview
    // showing fewer words after the fix is purely a CSS line-breaking effect.
    expect(bodyEl().textContent).toContain("- Area Chair (AC)");
  });
});
