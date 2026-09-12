import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { TicketAuditTrail } from "./TicketAuditTrail";
import type { EmailAuditTrailEntry } from "@/types";

// Given oldest-first (as the endpoint returns), the component must render
// newest-first.
const entries: EmailAuditTrailEntry[] = [
  {
    id: 1,
    email_id: "5",
    action: "classified",
    actor: "pipeline",
    timestamp: "2026-07-20T09:00:00Z",
    metadata: null,
  },
  {
    id: 2,
    email_id: "5",
    action: "approved",
    actor: "chair",
    timestamp: "2026-07-22T15:00:00Z",
    metadata: null,
  },
];

describe("TicketAuditTrail", () => {
  it("is collapsed by default and expands on click", async () => {
    render(<TicketAuditTrail entries={entries} />);
    // Header (with count) shows; entries are hidden until expanded.
    expect(screen.getByRole("button", { name: /Activity \(2\)/ })).toBeInTheDocument();
    expect(screen.queryByText("classified")).toBeNull();

    await userEvent.setup().click(screen.getByRole("button", { name: /Activity \(2\)/ }));
    expect(screen.getByText("classified")).toBeInTheDocument();
  });

  it("orders entries latest-first regardless of input order", async () => {
    render(<TicketAuditTrail entries={entries} />);
    await userEvent.setup().click(screen.getByRole("button", { name: /Activity \(2\)/ }));

    const items = screen.getAllByRole("listitem");
    // Input was [classified (07-20), approved (07-22)]; rendered newest-first.
    expect(items[0]).toHaveTextContent("approved");
    expect(items[1]).toHaveTextContent("classified");
  });
});

// ---------------------------------------------------------------------------
// Per-action metadata
//
// ⚠️ THIS COMPONENT IS THE INBOX'S, not this feature's. Its only mount is
// `/tickets/[ticketId]`, where the overwhelming majority of entries are actions
// this feature never introduced. So the regression half of this file — "an
// unhandled action renders exactly as before" — is not padding; it is the
// property that makes the addition safe at all.
// ---------------------------------------------------------------------------
const FORUM_ID = "ll0avn6ylq";
const NOTE_ID = "newCommentX";

function entry(overrides: Partial<EmailAuditTrailEntry> = {}): EmailAuditTrailEntry {
  return {
    id: 99,
    email_id: "5",
    action: "openreview_comment_posted",
    actor: "chair1",
    timestamp: "2026-09-01T12:00:00Z",
    metadata: null,
    ...overrides,
  };
}

/** Render expanded, since everything below is inside the collapsed list. */
async function renderExpanded(entries: EmailAuditTrailEntry[]) {
  render(<TicketAuditTrail entries={entries} />);
  await userEvent
    .setup()
    .click(screen.getByRole("button", { name: /Activity/ }));
  return screen.getAllByRole("listitem")[0];
}

describe("TicketAuditTrail — openreview_comment_posted", () => {
  const posted = (metadata: Record<string, unknown>) =>
    entry({ action: "openreview_comment_posted", metadata });

  it("links to the comment that was POSTED, not the one it answers", async () => {
    /* ⚠️ `note_id` is the new comment; `parent_note_id` is what it replies to.
       Both are same-shaped opaque tokens, so linking the wrong one produces a
       URL that opens a real page — just not the one this entry is about. */
    await renderExpanded([
      posted({
        note_id: NOTE_ID,
        parent_note_id: "parentAAAA",
        forum_id: FORUM_ID,
        visibility: "public",
        submission_number: 1030,
      }),
    ]);

    const link = screen.getByRole("link", { name: /view comment/i });
    expect(link).toHaveAttribute(
      "href",
      `https://openreview.net/forum?id=${FORUM_ID}&noteId=${NOTE_ID}`
    );
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });

  it("describes a public post by its audience, not by the word 'public'", async () => {
    await renderExpanded([
      posted({ note_id: NOTE_ID, forum_id: FORUM_ID, visibility: "public",
               submission_number: 1030 }),
    ]);

    expect(
      screen.getByText(/visible to the same people as the comment it answers/i)
    ).toBeInTheDocument();
    expect(screen.getByText(/on submission 1030/i)).toBeInTheDocument();
  });

  it("describes an internal post as the narrower audience", async () => {
    await renderExpanded([
      posted({ note_id: NOTE_ID, forum_id: FORUM_ID, visibility: "internal",
               submission_number: 1030 }),
    ]);

    expect(
      screen.getByText(/program chairs and the paper's authors/i)
    ).toBeInTheDocument();
  });

  it("still describes the post when the comment id is missing", async () => {
    /* `note_id` and `edit_id` are best-effort upstream and can be null on a
       genuinely SUCCESSFUL post, so a missing one must not blank the entry or
       render a broken link. */
    await renderExpanded([
      posted({ note_id: null, forum_id: FORUM_ID, visibility: "public",
               submission_number: 1030 }),
    ]);

    expect(screen.queryByRole("link")).toBeNull();
    expect(
      screen.getByText(/visible to the same people as the comment it answers/i)
    ).toBeInTheDocument();
  });

  it("renders no link and no crash when metadata is null", async () => {
    const row = await renderExpanded([posted({})]);

    expect(row).toHaveTextContent("openreview_comment_posted");
    expect(screen.queryByRole("link")).toBeNull();
  });
});

describe("TicketAuditTrail — the other actions this feature writes", () => {
  it("shows the dismissal reason", async () => {
    await renderExpanded([
      entry({
        action: "openreview_candidate_dismissed",
        metadata: {
          reason: "Quotes an old notification; not a reply to it.",
          openreview_note_id: "abc",
          lane: "human_review",
        },
      }),
    ]);

    expect(
      screen.getByText(/reason: quotes an old notification/i)
    ).toBeInTheDocument();
  });

  it("shows the gate's reason when a post was blocked", async () => {
    await renderExpanded([
      entry({
        action: "openreview_post_blocked",
        actor: "openreview_gate",
        metadata: { reason: "The reply text is empty; there is nothing to post." },
      }),
    ]);

    expect(screen.getByText(/the reply text is empty/i)).toBeInTheDocument();
  });

  it.each([
    ["OpenReviewNoteNotFoundError", /no longer exists/i],
    ["OpenReviewPermissionError", /openreview refused the post/i],
    ["OpenReviewThreadMismatchError", /different submission/i],
    ["SomeUnrecognisedError", /posting to openreview failed/i],
  ])("turns %s into a sentence", async (errorType, expected) => {
    await renderExpanded([
      entry({
        action: "openreview_post_failed",
        metadata: { stage: "post", error_type: errorType, error: "raw text" },
      }),
    ]);

    expect(screen.getByText(expected)).toBeInTheDocument();
    expect(screen.getByText(/nothing was posted/i)).toBeInTheDocument();
  });

  it("⚠️ never prints the exception class name", async () => {
    /* `error_type` selects a sentence and must not itself reach the screen: it
       is an internal identifier, and a chair reading
       "OpenReviewThreadMismatchError" learns nothing they can act on. */
    await renderExpanded([
      entry({
        action: "openreview_post_failed",
        metadata: {
          stage: "post",
          error_type: "OpenReviewThreadMismatchError",
          error: "Refusing to post: note X belongs to forum Y",
        },
      }),
    ]);

    expect(document.body.textContent).not.toMatch(/OpenReviewThreadMismatchError/);
    expect(document.body.textContent).not.toMatch(/error_type/);
  });

  it("names the status a successful auto-solve set", async () => {
    await renderExpanded([
      entry({
        action: "zendesk_auto_solved",
        metadata: { status_set: "solved", tags_added: ["ai_status_solved"],
                    zendesk_ticket_id: 21567 },
      }),
    ]);

    expect(screen.getByText(/ticket set to solved/i)).toBeInTheDocument();
  });

  it("says the reply IS posted when the auto-solve failed", async () => {
    /* ⚠️ The dangerous misreading is "the whole thing failed". The comment is
       live at this point; only the ticket is outstanding, and the line says so
       before it says anything else. */
    await renderExpanded([
      entry({
        action: "zendesk_auto_solve_failed",
        metadata: {
          error: "Zendesk returned 500",
          status_code: 500,
          openreview_post_state: "posted",
          ticket_state: "not solved",
        },
      }),
    ]);

    expect(
      screen.getByText(/the reply was posted, but the ticket could not be closed/i)
    ).toBeInTheDocument();
    expect(screen.getByText(/zendesk returned 500/i)).toBeInTheDocument();
  });

  it("explains a skipped auto-solve", async () => {
    await renderExpanded([
      entry({
        action: "zendesk_auto_solve_skipped",
        metadata: { reason: "ticket is closed" },
      }),
    ]);

    expect(
      screen.getByText(/no ticket action was needed — ticket is closed/i)
    ).toBeInTheDocument();
  });

  it("clips an unbounded error string", async () => {
    /* These come from `str(exc)` upstream and have no length contract; an
       unbounded one would stretch the collapsed list rather than merely look
       untidy. */
    const long = "z".repeat(600);
    const row = await renderExpanded([
      entry({ action: "zendesk_auto_solve_failed", metadata: { error: long } }),
    ]);

    expect(row.textContent!.length).toBeLessThan(long.length);
    expect(row.textContent).toContain("…");
  });
});

// ---------------------------------------------------------------------------
// ⚠️ REGRESSION: the Inbox's own entries are untouched
// ---------------------------------------------------------------------------
describe("TicketAuditTrail — unhandled actions are unchanged", () => {
  it.each([
    ["approved", { final_text: "Dear author, …", was_edited: true }],
    ["zendesk_sent", { ticket_id: 21567, public: false, tags: ["ai_drafted"] }],
    ["rerouted", { reason: "wrong lane", new_lane: "faq" }],
    ["chair_assigned", { chair_id: 3, rationale: "policy area match" }],
    // Part of THIS feature, but deliberately unhandled: its reason is
    // boilerplate that the `openreview_comment_posted` entry states better.
    ["openreview_post_authorized", { reason: "OpenReview reply candidate…" }],
  ])("adds nothing to %s, however rich its metadata", async (action, metadata) => {
    const row = await renderExpanded([entry({ action, metadata })]);

    // Exactly the three original pieces, and nothing else.
    expect(row.textContent).toContain(action);
    expect(row.textContent).toContain("chair1");
    for (const value of Object.values(metadata)) {
      expect(row.textContent).not.toContain(String(value));
    }
    expect(row.textContent).not.toMatch(/[{}]/);
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("⚠️ never dumps raw JSON for an action it does not know", async () => {
    /* The failure this design exists to prevent: a generic renderer would start
       showing whatever a future audit call happens to attach, including
       internal field names and secrets nobody reviewed for a chair's eyes. */
    const row = await renderExpanded([
      entry({
        action: "some_future_action_nobody_has_written_yet",
        metadata: { internal_token: "s3cr3t", nested: { deep: [1, 2, 3] } },
      }),
    ]);

    expect(row.textContent).not.toContain("s3cr3t");
    expect(row.textContent).not.toContain("internal_token");
    expect(row.textContent).not.toMatch(/[[\]{}]/);
  });

  it("survives metadata that is not an object", async () => {
    const row = await renderExpanded([
      entry({
        action: "openreview_candidate_dismissed",
        metadata: "not an object" as unknown as Record<string, unknown>,
      }),
    ]);

    expect(row).toHaveTextContent("openreview_candidate_dismissed");
  });

  it("survives a handled action whose expected key is missing", async () => {
    const row = await renderExpanded([
      entry({ action: "openreview_candidate_dismissed", metadata: { lane: "faq" } }),
    ]);

    expect(row).toHaveTextContent("openreview_candidate_dismissed");
    expect(row.textContent).not.toContain("Reason:");
    expect(row.textContent).not.toContain("faq");
  });
});
