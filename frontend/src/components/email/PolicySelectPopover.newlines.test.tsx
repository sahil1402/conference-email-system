/**
 * PolicySelectPopover — the SEARCH-RESULT preview preserves source newlines (1c).
 *
 * Distinct element from the detail/confirm view, which already had pre-wrap
 * (+ wordBreak) and is covered in PolicySelectPopover.test.tsx. This file
 * guards the listbox option preview only.
 *
 * Setup mirrors PolicySelectPopover.test.tsx — API-layer spy on `listPolicies`
 * so the real debounce, the real usePolicies hook and the real Radix Dialog all
 * run. Its helpers are module-local and un-exported, so they are re-stated here
 * rather than imported (importing would mean editing that file).
 *
 * SCOPE LIMIT: jsdom performs no layout. These tests pin the `white-space`
 * DECLARATION and the `line-clamp-2` class — they cannot prove the newlines
 * visually break onto separate lines. A `textContent` assertion is likewise
 * identical with or without the fix (verified in 1a-ii), so it proves data
 * integrity through the render, never the CSS behaviour. `toHaveStyle` is the
 * only assertion here that catches the bug.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { PolicySelectPopover } from "@/components/email/PolicySelectPopover";
import type { PolicyDocument } from "@/types";

const state = vi.hoisted(() => ({ list: vi.fn(), retry: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, listPolicies: state.list, retryEmail: state.retry };
});

const EMAIL_ID = 2662;
const TITLE = "Program committee roles and structure";

/**
 * Mirrors a real KB body: a lead-in line then bullets separated by a SINGLE
 * "\n", no blank lines. That shape is the point — the CSS default
 * (`white-space: normal`, what this preview rendered with before the fix)
 * collapses those newlines into spaces, and any blank-line-dependent approach
 * would not fix it either.
 */
const LEAD_IN = "Reviews are due within 10 days, no exceptions:";
const BULLET_CONTENT = [
  LEAD_IN,
  "- Program Committee member (PC/Reviewer).",
  "- Senior Program Committee Member (SPC).",
].join("\n");

function policy(over: Partial<PolicyDocument> = {}): PolicyDocument {
  return {
    policy_key: "int_program-commitee-roles-and-structure",
    title: TITLE,
    content: BULLET_CONTENT,
    category: "roles",
    visibility: "internal",
    status: "active",
    source: null,
    updated_at: null,
    supersedes: null,
    superseded_by: null,
    root_key: null,
    version: 1,
    ...over,
  } as PolicyDocument;
}

function renderPicker() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={qc}>
      <PolicySelectPopover emailId={EMAIL_ID} onSelect={vi.fn()} >
        <button type="button">Add policy</button>
      </PolicySelectPopover>
    </QueryClientProvider>
  );
}

/**
 * Drive the component the way a chair does: open the Radix Dialog from its
 * trigger, type into the real search box, wait out the real debounce. Results
 * never render for an empty query, so there is no way to reach the preview
 * without this flow.
 */
async function openAndSearch(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Add policy" }));
  const input = screen.getByRole("combobox", { name: "Search policies" });
  await user.type(input, "roles");
  await screen.findByText(TITLE);
}

/** The preview <p> — plain text on this branch, so it owns the text node. */
function previewEl(): HTMLElement {
  const el = screen.getByText(new RegExp(LEAD_IN.slice(0, 30)));
  const p = el.closest("p");
  if (!p) throw new Error("preview <p> not found");
  return p;
}

beforeEach(() => {
  state.list.mockReset();
  state.list.mockResolvedValue({ policies: [policy()] });
});

describe("PolicySelectPopover — search-result preview preserves source newlines", () => {
  it("renders the preview with white-space: pre-wrap", async () => {
    const user = userEvent.setup();
    renderPicker();
    await openAndSearch(user);

    expect(previewEl()).toHaveStyle({ whiteSpace: "pre-wrap" });
  });

  it("keeps the literal newlines in the rendered preview text", async () => {
    const user = userEvent.setup();
    renderPicker();
    await openAndSearch(user);

    // textContent, not toHaveTextContent: RTL normalises whitespace, so the
    // newlines would be invisible to that matcher. Data integrity only — see
    // the SCOPE LIMIT note at the top of this file.
    const text = previewEl().textContent ?? "";
    expect((text.match(/\n/g) ?? []).length).toBe(2);
    expect(text).toContain("\n- Program Committee member");
    expect(text).not.toContain("exceptions: - Program Committee member");
  });
});

describe("PolicySelectPopover — preview truncation is unchanged by the newline fix", () => {
  it("clamps the preview to two lines, unconditionally at this site", async () => {
    // NOTE: unlike the PolicyList card and the AddPolicyPanel preview, this site
    // has NO expand/collapse toggle — the clamp is static, so there is no
    // second state to check. Asserting the toggle's absence pins that
    // difference, so a future "add Show more here too" change has to come with
    // its own expanded-state pre-wrap guard rather than silently inheriting
    // this one.
    const user = userEvent.setup();
    renderPicker();
    await openAndSearch(user);

    expect(previewEl()).toHaveClass("line-clamp-2");
    expect(screen.queryByRole("button", { name: /show more|show less/i })).toBeNull();
  });
});

describe("PolicySelectPopover — Radix Dialog remount (site-specific)", () => {
  it("re-applies pre-wrap after the dialog is closed and reopened", async () => {
    // Radix unmounts DialogContent on close, so the preview is destroyed and
    // rebuilt on every reopen. This exercises the real trigger flow twice and
    // pins that the style survives the remount rather than only being right on
    // the dialog's first mount.
    const user = userEvent.setup();
    renderPicker();
    await openAndSearch(user);

    const dialog = screen.getByRole("dialog");
    // The preview really is inside the dialog, not a stray match elsewhere.
    expect(within(dialog).getByText(new RegExp(LEAD_IN.slice(0, 30)))).toBeInTheDocument();

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    // Full flow again, including retyping: the component clears search state on
    // close by design ("never reopens mid-search"), so a reopen genuinely
    // rebuilds the results list from scratch.
    await openAndSearch(user);

    const reopened = previewEl();
    expect(reopened).toHaveStyle({ whiteSpace: "pre-wrap" });
    expect(reopened).toHaveClass("line-clamp-2");
    // Content intact across the remount, newlines included.
    expect((reopened.textContent?.match(/\n/g) ?? []).length).toBe(2);
  });
});
