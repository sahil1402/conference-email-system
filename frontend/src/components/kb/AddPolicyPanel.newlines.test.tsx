/**
 * AddPolicyPanel — the similar-policy preview preserves source newlines (1b).
 *
 * Spies at the API layer (`findSimilarPolicies`) so the preview is reached
 * through the real useFindSimilar hook and the panel's own "Check for related
 * policies" flow, matching PolicySelectPopover.test.tsx.
 *
 * SCOPE LIMIT: jsdom performs no layout. These tests pin the `white-space`
 * DECLARATION and the `line-clamp-2` class — they cannot prove that the
 * newlines visually break onto separate lines. A `textContent` assertion is
 * likewise identical with or without the fix (verified in 1a-ii), so it proves
 * data integrity through the render, never the CSS behaviour. `toHaveStyle` is
 * the only assertion here that catches the bug.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { AddPolicyPanel } from "./AddPolicyPanel";
import type { ConflictReport, SimilarPolicy } from "@/types";

const state = vi.hoisted(() => ({ findSimilar: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, findSimilarPolicies: state.findSimilar };
});

/**
 * Mirrors a real KB body: a lead-in line followed by bullets separated by a
 * SINGLE "\n", with no blank lines anywhere. That shape is the point — the CSS
 * default (`white-space: normal`, what this preview rendered with before the
 * fix) collapses those newlines into spaces, and any blank-line-dependent
 * approach would not fix it either.
 */
const LEAD_IN = "Reviews are due within 10 days, no exceptions:";
const BULLET_CONTENT = [
  LEAD_IN,
  "- Program Committee member (PC/Reviewer).",
  "- Senior Program Committee Member (SPC).",
].join("\n");

function similarPolicy(over: Partial<SimilarPolicy> = {}): SimilarPolicy {
  return {
    policy_key: "int_program-commitee-roles-and-structure",
    title: "Program committee roles and structure",
    score: 0.82,
    content: BULLET_CONTENT,
    ...over,
  };
}

function report(over: Partial<ConflictReport> = {}): ConflictReport {
  return {
    checked_at: "2026-07-29T00:00:00Z",
    available: true,
    summary: "One conflict found.",
    candidates_checked: ["int_program-commitee-roles-and-structure"],
    conflicts: [],
    ...over,
  };
}

function renderPanel(
  similar: SimilarPolicy[] = [similarPolicy()],
  conflict_report: ConflictReport | null = null
) {
  state.findSimilar.mockResolvedValue({ similar, conflict_report });
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={qc}>
      {/* The draft the chair is typing. Deliberately shares NO wording with
          BULLET_CONTENT, so text queries can never hit the form's own textarea
          instead of the preview below it. */}
      <AddPolicyPanel
        title="Draft heading"
        content="Draft body still being written."
        category=""
        setTitle={vi.fn()}
        setContent={vi.fn()}
        setCategory={vi.fn()}
        onClose={vi.fn()}
        onCreated={vi.fn()}
      />
    </QueryClientProvider>
  );
}

/** Run the panel's own check flow so the similar list actually mounts. */
async function showSimilar(user: ReturnType<typeof userEvent.setup>) {
  await user.click(
    screen.getByRole("button", { name: /check for related policies/i })
  );
  await screen.findByText(/Related existing policies/);
}

/**
 * The preview <p>, which owns the style.
 *
 * Resolved via closest("p") because the element carrying the text differs by
 * branch: plain content sits directly in the <p>, while the conflict branch
 * wraps it in HighlightText's <span>. Anchoring on LEAD_IN (never highlighted
 * in these fixtures) keeps the lookup valid in both.
 */
function previewEl(): HTMLElement {
  const el = screen.getByText(new RegExp("Reviews are due within 10 days"));
  const p = el.closest("p");
  if (!p) throw new Error("preview <p> not found");
  return p;
}

beforeEach(() => {
  state.findSimilar.mockReset();
});

describe("AddPolicyPanel — similar-policy preview preserves source newlines", () => {
  it("renders the preview with white-space: pre-wrap", async () => {
    const user = userEvent.setup();
    renderPanel();
    await showSimilar(user);

    expect(previewEl()).toHaveStyle({ whiteSpace: "pre-wrap" });
  });

  it("applies pre-wrap in BOTH the collapsed and expanded states", async () => {
    // Guards the two render branches independently: the clamp is toggled on the
    // same element, so a future edit could restore the newline-eating default
    // in one state while the other still looks right.
    const user = userEvent.setup();
    renderPanel();
    await showSimilar(user);

    expect(previewEl()).toHaveStyle({ whiteSpace: "pre-wrap" });

    await user.click(screen.getByRole("button", { name: /show more/i }));

    expect(previewEl()).toHaveStyle({ whiteSpace: "pre-wrap" });
  });

  it("keeps the literal newlines in the rendered text", async () => {
    const user = userEvent.setup();
    renderPanel();
    await showSimilar(user);

    // textContent, not toHaveTextContent: RTL normalises whitespace, so the
    // newlines would be invisible to that matcher. Data integrity only — see
    // the SCOPE LIMIT note at the top of this file.
    const text = previewEl().textContent ?? "";
    expect(text).toContain("\n- Program Committee member");
    expect(text).toContain("\n- Senior Program Committee Member");
    expect(text).not.toContain("exceptions: - Program Committee member");
  });
});

describe("AddPolicyPanel — truncation behaviour is unchanged by the newline fix", () => {
  it("clamps to two lines while collapsed and drops the clamp once expanded", async () => {
    // The paired half of the guard above: pre-wrap and line-clamp-2 live on the
    // same element, so this pins the clamp so it cannot be silently dropped
    // while "fixing" newlines (or re-added on the expanded branch).
    const user = userEvent.setup();
    renderPanel();
    await showSimilar(user);

    expect(previewEl()).toHaveClass("line-clamp-2");

    const toggle = screen.getByRole("button", { name: /show more/i });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);

    expect(previewEl()).not.toHaveClass("line-clamp-2");
    expect(screen.getByRole("button", { name: /show less/i })).toHaveAttribute(
      "aria-expanded",
      "true"
    );
  });
});

describe("AddPolicyPanel — HighlightText branch (site-specific)", () => {
  it("keeps pre-wrap on the parent when a highlighted snippet SPANS a newline", async () => {
    // Branch 3 of the 1b-i compatibility check, pinned. The snippet straddles a
    // "\n", so the newline ends up INSIDE the <mark> rather than in a sibling
    // text node — the one arrangement where a child overriding white-space
    // would collapse a break that the parent's pre-wrap otherwise governs.
    const user = userEvent.setup();
    const spanning = "exceptions:\n- Program Committee member";
    expect(BULLET_CONTENT).toContain(spanning); // snippets are verbatim substrings

    renderPanel(
      [similarPolicy()],
      report({
        conflicts: [
          {
            policy_key: "int_program-commitee-roles-and-structure",
            title: "Program committee roles and structure",
            explanation: "Contradicts the 10-day rule.",
            snippets: [spanning],
          },
        ],
      })
    );
    await showSimilar(user);

    const preview = previewEl();
    expect(preview).toHaveStyle({ whiteSpace: "pre-wrap" });

    // The highlight really is active on this branch (else the rest is vacuous).
    const mark = preview.querySelector("mark");
    expect(mark).not.toBeNull();

    // Data integrity, not layout: every source newline survives the split into
    // <mark>/Fragment children, including the one inside the <mark>.
    expect((preview.textContent?.match(/\n/g) ?? []).length).toBe(2);
    expect(mark?.textContent).toContain("\n- Program Committee member");

    // The parent's pre-wrap is only sufficient because no child redeclares
    // white-space — inheritance is what carries it into the <mark>.
    expect(mark?.style.whiteSpace).toBe("");
  });
});
