import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { SuggestionList } from "./SuggestionList";
import type { PolicySuggestion } from "@/types";

/**
 * SCOPE LIMIT — jsdom performs NO layout, so no test in this suite can prove
 * the newlines visually break. `toHaveStyle` pins the `white-space` declaration
 * (the only assertion that actually catches the bug) and the clamp test pins
 * `line-clamp-2` so the two cannot silently trade places. A `textContent`
 * assertion passes with OR without the fix — see the note on that test.
 * Visual confirmation still needs a human; there is no browser driver here.
 *
 * Same bug class already fixed in PolicyList / AddPolicyPanel /
 * PolicySelectPopover; SuggestionList landed after that pass and was missed.
 */

/**
 * Shaped like real CEL-condensed content (experience_learning.py): a lead-in
 * line followed by items separated by a SINGLE "\n" — no blank lines anywhere.
 * That shape is the point: `white-space: normal` (the CSS default, and what
 * this row rendered with before the fix) collapses those newlines into spaces,
 * and any blank-line-dependent approach (markdown paragraphs) would not help.
 */
const MULTILINE_CONTENT = [
  "Late registration is handled case by case:",
  "- Requests within 7 days of the deadline are granted automatically.",
  "- Later requests need the registration chair's approval.",
  "- Fee waivers are never granted retroactively.",
].join("\n");

function makeSuggestion(overrides: Partial<PolicySuggestion> = {}): PolicySuggestion {
  return {
    id: 1,
    source_email_id: 42,
    source_zendesk_ticket_id: 123,
    experience_summary: "Chair clarified the late-registration grace period.",
    title: "Late registration handling",
    content: MULTILINE_CONTENT,
    category: null,
    intents: [],
    generalizable: true,
    reason: null,
    confidence: 0.8,
    conflict_report: null,
    seen_count: 1,
    status: "pending",
    created_at: "2026-07-29T00:00:00Z",
    ...overrides,
  };
}

function renderList(suggestions: PolicySuggestion[] = [makeSuggestion()]) {
  render(
    <SuggestionList suggestions={suggestions} onSelect={vi.fn()} onReject={vi.fn()} />,
  );
}

/** The content <p> — matched on its first line, which it owns as a text node. */
function contentEl(): HTMLElement {
  return screen.getByText(/Late registration is handled case by case/);
}

describe("SuggestionList — suggestion content preserves source newlines", () => {
  it("renders the content with white-space: pre-wrap", () => {
    renderList();

    expect(contentEl()).toHaveStyle({ whiteSpace: "pre-wrap" });
  });

  it("applies pre-wrap to every row, not just the first", () => {
    // The style sits inside the .map, so a regression would drop it from all
    // rows at once — but pinning a second row guards against a future per-row
    // branch (e.g. a selected/expanded variant) reintroducing the default.
    renderList([
      makeSuggestion({ id: 1 }),
      makeSuggestion({ id: 2, content: `Second row:\n- also multi-line.` }),
    ]);

    expect(contentEl()).toHaveStyle({ whiteSpace: "pre-wrap" });
    expect(screen.getByText(/Second row:/)).toHaveStyle({ whiteSpace: "pre-wrap" });
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
    const text = contentEl().textContent ?? "";
    expect(text).toContain("\n- Requests within 7 days");
    expect(text).toContain("\n- Later requests need");
    expect(text).not.toContain("case by case: - Requests within 7 days");
  });
});

describe("SuggestionList — truncation behaviour is unchanged by the newline fix", () => {
  it("keeps the two-line clamp on the content", () => {
    // The paired half of the guard above: pre-wrap and line-clamp-2 live on the
    // same element. The clamp compiles to `overflow: hidden` + `-webkit-box`,
    // so it caps the height at two line boxes regardless of how many newlines
    // pre-wrap now renders — pinning it proves the fix did not trade a
    // run-on paragraph for an unbounded row.
    renderList();

    expect(contentEl().className).toContain("line-clamp-2");
  });

  it("leaves the title untouched (not in scope for the newline fix)", () => {
    // Titles are short by convention and were deliberately excluded, so this
    // pins the boundary: a future edit that blanket-applies pre-wrap to the
    // whole row should have to justify changing this expectation.
    renderList();

    expect(screen.getByText("Late registration handling")).not.toHaveStyle({
      whiteSpace: "pre-wrap",
    });
  });
});
