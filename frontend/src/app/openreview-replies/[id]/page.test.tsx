/**
 * OpenReview reply detail — review pane.
 *
 * Mocks the API layer (`@/lib/api`) and lets the real hook and React Query run,
 * matching the list page's convention, so loading/not-found/error come from
 * genuine query states rather than a stubbed hook.
 *
 * ⚠️ The link assertions check the WHOLE url, not that a link exists. Both ids
 * are opaque tokens of similar shape, so swapping them — or dropping the
 * `noteId` and linking to the forum root — produces a link that renders, opens,
 * and lands on a real OpenReview page, just the WRONG comment. That is invisible
 * in review, so it is pinned by value.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import type { Email, EmailDetailResponse } from "@/types";

const getEmailById = vi.hoisted(() => vi.fn());
const postOpenReviewReply = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, getEmailById, postOpenReviewReply };
});

import OpenReviewReplyDetailPage from "./page";

const FORUM_ID = "ll0avn6ylq";
const NOTE_ID = "jnHgRMHgrm";
const VENUE = "AAAI.org/2027";
/** Two ids sharing a venue prefix — the shape the prefix dimming operates on. */
const READER_GROUPS = [
  `${VENUE}/Program_Chairs`,
  `${VENUE}/Submission1030/Reviewers`,
];
const REPLY = "Thank you — I will submit my review by Friday.";
/** Only in `body`; must never surface, since the page shows the extracted text. */
const RAW_BODY = `${REPLY}\n\n-----Original Message-----\nQUOTED_SENTINEL`;

function email(overrides: Partial<Email> = {}): Email {
  return {
    id: 7,
    sender: "reviewer@example.edu",
    sender_name: "Wei Zhang",
    subject: "Re: [AAAI 2027] SPC commented on a paper",
    body: RAW_BODY,
    status: "DRAFT_GENERATED",
    received_at: "2026-08-27T15:28:28Z",
    assigned_chair_id: null,
    classification: null,
    routing: null,
    draft: null,
    created_at: "2026-08-27T15:28:28Z",
    updated_at: "2026-08-27T15:28:28Z",
    extraction: {
      submission_numbers: ["1030"],
      openreview_forum_ids: [FORUM_ID],
      openreview_note_id: NOTE_ID,
      openreview_notification_sender: "aaai2027-notifications@openreview.net",
      openreview_reply_candidate: true,
      extracted_reply_text: REPLY,
      authors: [],
      method: "llm_distiller",
    },
    openreview_readers: {
      state: "fetched",
      readers: READER_GROUPS,
      note_id: NOTE_ID,
      error: null,
      error_type: null,
    },
    ...overrides,
  } as unknown as Email;
}

/** Override just the readers block, leaving the rest of the fixture alone. */
const withReaders = (readers: unknown) =>
  email({ openreview_readers: readers } as unknown as Partial<Email>);

const groupList = () =>
  screen.queryByRole("list", { name: /reader groups on the original comment/i });

const detail = (e: Email): EmailDetailResponse =>
  ({ email: e, audit_trail: [] }) as unknown as EmailDetailResponse;

function Wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const renderPage = (id = "7") =>
  render(<OpenReviewReplyDetailPage params={{ id }} />, { wrapper: Wrapper });

const editor = () => screen.getByRole("textbox", { name: /reply to post/i });

beforeEach(() => {
  getEmailById.mockReset();
  postOpenReviewReply.mockReset();
});

// ---------------------------------------------------------------------------
// Submit-action fixtures
// ---------------------------------------------------------------------------
const postButton = () =>
  screen.getByRole("button", { name: /approve & post/i });

/** A 200 response. `outcome` drives which of the four ticket fates it carries. */
function posted(
  outcome: "solved" | "solve_failed" | "skipped_no_ticket" | "skipped_closed" =
    "solved",
  overrides: Record<string, unknown> = {}
) {
  return {
    ...email(),
    openreview_post: {
      state: "posted",
      note_id: "new-note-1",
      edit_id: "edit-1",
      parent_note_id: NOTE_ID,
      forum_id: FORUM_ID,
      venue_id: VENUE,
      submission_number: 1030,
      visibility: "public",
      readers: READER_GROUPS,
      ...((overrides.openreview_post as object) ?? {}),
    },
    ticket_resolution: {
      outcome,
      attempted: outcome === "solved" || outcome === "solve_failed",
      ticket_id: 21567,
      zendesk_status: outcome === "solved" ? "solved" : null,
      error: outcome === "solve_failed" ? "Zendesk returned 500" : null,
      error_type: outcome === "solve_failed" ? "ZendeskSendError" : null,
      // ⚠️ NON-NULL FOR EVERY NON-`solved` OUTCOME, including the benign skips.
      recovery: outcome === "solved" ? null : "…next step…",
    },
    warning: outcome === "solved" ? null : "…next step…",
  };
}

/** The ApiError shape the axios interceptor produces for a structured detail. */
const apiError = (status: number, detail: Record<string, string>) => ({
  status,
  detail: JSON.stringify(detail),
  data: detail,
});

// ---------------------------------------------------------------------------
// Read-only context
// ---------------------------------------------------------------------------
describe("detail — context", () => {
  it("fetches the email by the id in the route", async () => {
    getEmailById.mockResolvedValue(detail(email()));

    renderPage("42");

    await waitFor(() => expect(getEmailById).toHaveBeenCalledWith("42"));
  });

  it("shows the subject, sender, received date and paper", async () => {
    getEmailById.mockResolvedValue(detail(email()));

    renderPage();

    expect(
      await screen.findByRole("heading", {
        level: 1,
        name: "Re: [AAAI 2027] SPC commented on a paper",
      })
    ).toBeInTheDocument();
    expect(screen.getByText("Wei Zhang")).toBeInTheDocument();
    expect(screen.getByText("Submission 1030")).toBeInTheDocument();
    expect(screen.getByText(/2026/)).toBeInTheDocument();
  });

  it("falls back to the sender address when there is no display name", async () => {
    getEmailById.mockResolvedValue(detail(email({ sender_name: null })));

    renderPage();

    expect(await screen.findByText("reviewer@example.edu")).toBeInTheDocument();
  });

  it("links back to the list", async () => {
    getEmailById.mockResolvedValue(detail(email()));

    renderPage();

    expect(
      await screen.findByRole("link", { name: /back to openreview replies/i })
    ).toHaveAttribute("href", "/openreview-replies");
  });
});

// ---------------------------------------------------------------------------
// The OpenReview link
// ---------------------------------------------------------------------------
describe("detail — link to the original comment", () => {
  it("builds forum?id=<forum>&noteId=<note> exactly", async () => {
    getEmailById.mockResolvedValue(detail(email()));

    renderPage();

    const link = await screen.findByRole("link", {
      name: /original comment on openreview/i,
    });
    expect(link).toHaveAttribute(
      "href",
      `https://openreview.net/forum?id=${FORUM_ID}&noteId=${NOTE_ID}`
    );
  });

  it("puts the FORUM id in id= and the NOTE id in noteId=, not the reverse", async () => {
    /* The two are same-shaped opaque tokens, so a swap still produces a valid
       URL to a real page — just the wrong one. Asserted per-parameter so the
       swap cannot pass. */
    getEmailById.mockResolvedValue(detail(email()));

    renderPage();

    const href = (
      await screen.findByRole("link", { name: /original comment on openreview/i })
    ).getAttribute("href")!;
    const url = new URL(href);

    expect(url.searchParams.get("id")).toBe(FORUM_ID);
    expect(url.searchParams.get("noteId")).toBe(NOTE_ID);
  });

  it("opens in a new tab, safely", async () => {
    getEmailById.mockResolvedValue(detail(email()));

    renderPage();

    const link = await screen.findByRole("link", {
      name: /original comment on openreview/i,
    });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });

  it("says why there is no link when the note id is missing", async () => {
    /* Withheld rather than degraded to a forum-root link: that would open the
       discussion at the top and leave the chair guessing which comment this
       answers. */
    getEmailById.mockResolvedValue(
      detail(
        email({
          extraction: { ...email().extraction!, openreview_note_id: null },
        })
      )
    );

    renderPage();

    expect(
      await screen.findByText(/didn't carry both a forum id and a note id/i)
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: /original comment on openreview/i })
    ).toBeNull();
  });

  it("says why there is no link when the forum id is missing", async () => {
    getEmailById.mockResolvedValue(
      detail(
        email({
          extraction: { ...email().extraction!, openreview_forum_ids: [] },
        })
      )
    );

    renderPage();

    expect(
      await screen.findByText(/didn't carry both a forum id and a note id/i)
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// The editable reply
// ---------------------------------------------------------------------------
describe("detail — reply editor", () => {
  it("pre-populates with extracted_reply_text", async () => {
    getEmailById.mockResolvedValue(detail(email()));

    renderPage();

    await waitFor(() => expect(editor()).toHaveValue(REPLY));
  });

  it("shows the extracted text, never the raw quoted body", async () => {
    getEmailById.mockResolvedValue(detail(email()));

    renderPage();

    await waitFor(() => expect(editor()).toHaveValue(REPLY));
    expect(editor()).not.toHaveValue(RAW_BODY);
    expect(screen.queryByText(/QUOTED_SENTINEL/)).toBeNull();
  });

  it("is actually editable", async () => {
    const user = userEvent.setup();
    getEmailById.mockResolvedValue(detail(email()));

    renderPage();
    await waitFor(() => expect(editor()).toHaveValue(REPLY));

    await user.clear(editor());
    await user.type(editor(), "Rewritten by the chair.");

    expect(editor()).toHaveValue("Rewritten by the chair.");
  });

  it("renders an empty extraction as an empty box, not an error", async () => {
    /* A body that was entirely quoted material extracts to "" — a real state
       the quote stripping produces correctly, so it must not read as a
       failure. */
    getEmailById.mockResolvedValue(
      detail(
        email({
          extraction: { ...email().extraction!, extracted_reply_text: "" },
        })
      )
    );

    renderPage();

    await waitFor(() => expect(editor()).toHaveValue(""));
    expect(screen.queryByText(/couldn't load/i)).toBeNull();
    expect(
      screen.getByText(/appears to be entirely quoted content/i)
    ).toBeInTheDocument();
  });

  it("keeps the empty box typable", async () => {
    const user = userEvent.setup();
    getEmailById.mockResolvedValue(
      detail(
        email({
          extraction: { ...email().extraction!, extracted_reply_text: "" },
        })
      )
    );

    renderPage();
    await waitFor(() => expect(editor()).toHaveValue(""));

    await user.type(editor(), "Written from scratch.");

    expect(editor()).toHaveValue("Written from scratch.");
  });

  it("shows no empty-extraction note when there IS reply text", async () => {
    getEmailById.mockResolvedValue(detail(email()));

    renderPage();
    await waitFor(() => expect(editor()).toHaveValue(REPLY));

    expect(
      screen.queryByText(/appears to be entirely quoted content/i)
    ).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Who can see the original comment
//
// ⚠️ Group ids are asserted on the LIST ITEM's textContent, never with
// `getByText(id)`. The component splits each id into two spans (shared venue
// prefix dimmed, tail at full weight), so a whole-string text query matches
// nothing even when the id renders perfectly. Asserting the reassembled
// textContent is what proves the split is cosmetic — that nothing was dropped,
// truncated or reordered on the way to the screen.
// ---------------------------------------------------------------------------
describe("detail — original comment audience", () => {
  it("lists the fetched reader groups", async () => {
    getEmailById.mockResolvedValue(detail(email()));

    renderPage();

    await screen.findByText(/who can see the original comment/i);
    const items = within(groupList()!).getAllByRole("listitem");
    expect(items.map((li) => li.textContent)).toEqual(READER_GROUPS);
  });

  it("shows each group id in FULL, never a prettified name", async () => {
    /* The whole point of the presentation decision: dimming the shared venue
       prefix must not shorten or rename anything. A friendly "Reviewers" label
       would be a claim about who is included, on the page where a chair decides
       exactly that. */
    getEmailById.mockResolvedValue(detail(email()));

    renderPage();

    const list = await screen.findByRole("list", {
      name: /reader groups on the original comment/i,
    });
    for (const id of READER_GROUPS) {
      expect(list.textContent).toContain(id);
    }
  });

  it("keeps every group distinguishable when they share a long prefix", async () => {
    /* Guards the prefix scan: it must stop a segment short, so two ids under the
       same submission never collapse to identical-looking rows. */
    const siblings = [
      `${VENUE}/Submission1030/Reviewers`,
      `${VENUE}/Submission1030/Area_Chairs`,
    ];
    getEmailById.mockResolvedValue(
      detail(
        withReaders({
          state: "fetched",
          readers: siblings,
          note_id: NOTE_ID,
          error: null,
          error_type: null,
        })
      )
    );

    renderPage();

    await screen.findByText(/who can see the original comment/i);
    const items = within(groupList()!).getAllByRole("listitem");
    expect(items.map((li) => li.textContent)).toEqual(siblings);
    expect(items[0].textContent).not.toEqual(items[1].textContent);
  });

  it("says the audience could not be confirmed when the fetch failed", async () => {
    getEmailById.mockResolvedValue(
      detail(
        withReaders({
          state: "failed",
          readers: null,
          note_id: NOTE_ID,
          error: "OpenReview did not respond within 10s.",
          error_type: "TimeoutError",
        })
      )
    );

    renderPage();

    expect(
      await screen.findByText(/couldn't confirm the audience/i)
    ).toBeInTheDocument();
    expect(
      screen.getByText(/openreview did not respond within 10s/i)
    ).toBeInTheDocument();
  });

  it("renders NO group list on failure — absent, not empty", async () => {
    /* ⚠️ THE DISCRIMINATOR between the two non-happy states. "We could not find
       out" and "nobody can see it" are opposite facts; an empty list rendered
       for a failure would show a real error as a reassuring answer. */
    getEmailById.mockResolvedValue(
      detail(
        withReaders({
          state: "failed",
          readers: null,
          note_id: NOTE_ID,
          error: "boom",
          error_type: "OpenReviewAPIError",
        })
      )
    );

    renderPage();
    await screen.findByText(/couldn't confirm the audience/i);

    expect(groupList()).toBeNull();
    expect(screen.queryByText(/lists no reader groups/i)).toBeNull();
  });

  it("distinguishes a genuinely empty audience from a failed lookup", async () => {
    /* The fourth fact: fetched, and the note names no groups. It gets its own
       wording, and must not borrow the failure's. */
    getEmailById.mockResolvedValue(
      detail(
        withReaders({
          state: "fetched",
          readers: [],
          note_id: NOTE_ID,
          error: null,
          error_type: null,
        })
      )
    );

    renderPage();

    expect(
      await screen.findByText(/lists no reader groups/i)
    ).toBeInTheDocument();
    expect(screen.queryByText(/couldn't confirm the audience/i)).toBeNull();
  });

  it("renders nothing at all when the email is not a candidate", async () => {
    getEmailById.mockResolvedValue(
      detail(
        withReaders({
          state: "not_applicable",
          readers: null,
          note_id: null,
          error: null,
          error_type: null,
        })
      )
    );

    renderPage();
    await waitFor(() => expect(editor()).toHaveValue(REPLY));

    expect(screen.queryByText(/who can see the original comment/i)).toBeNull();
    expect(groupList()).toBeNull();
  });

  it("renders nothing when the field is absent entirely", async () => {
    /* An older backend, or a response predating the field. Treated as
       not-applicable rather than crashing or guessing. */
    getEmailById.mockResolvedValue(
      detail(email({ openreview_readers: undefined } as unknown as Partial<Email>))
    );

    renderPage();
    await waitFor(() => expect(editor()).toHaveValue(REPLY));

    expect(screen.queryByText(/who can see the original comment/i)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Visibility choice
// ---------------------------------------------------------------------------
const publicOption = () => screen.getByRole("radio", { name: /^public/i });
const internalOption = () => screen.getByRole("radio", { name: /^internal/i });
const audience = () => screen.getByTestId("reply-audience");

describe("detail — visibility choice", () => {
  it("offers exactly two options and defaults to Public", async () => {
    getEmailById.mockResolvedValue(detail(email()));

    renderPage();

    await waitFor(() => expect(publicOption()).toBeChecked());
    expect(internalOption()).not.toBeChecked();
    expect(screen.getAllByRole("radio")).toHaveLength(2);
  });

  it("explains each option in plain language", async () => {
    getEmailById.mockResolvedValue(detail(email()));

    renderPage();

    expect(
      await screen.findByText(/same audience as the original comment/i)
    ).toBeInTheDocument();
    expect(
      screen.getByText(/only the program chairs and this paper's authors/i)
    ).toBeInTheDocument();
  });

  it("selects Internal when clicked, and deselects Public", async () => {
    const user = userEvent.setup();
    getEmailById.mockResolvedValue(detail(email()));

    renderPage();
    await waitFor(() => expect(publicOption()).toBeChecked());

    await user.click(internalOption());

    expect(internalOption()).toBeChecked();
    expect(publicOption()).not.toBeChecked();
  });

  it("switches back to Public", async () => {
    const user = userEvent.setup();
    getEmailById.mockResolvedValue(detail(email()));

    renderPage();
    await waitFor(() => expect(publicOption()).toBeChecked());

    await user.click(internalOption());
    await user.click(publicOption());

    expect(publicOption()).toBeChecked();
    expect(internalOption()).not.toBeChecked();
  });

  it("keeps the choice available right up to the submit control", async () => {
    /* Inverted from "still wires no submit control" now that commit 16 landed
       the action. The property that still matters: the choice and the button
       that acts on it are on screen together, so the visibility in effect is
       never off-screen at the moment of posting. */
    const user = userEvent.setup();
    getEmailById.mockResolvedValue(detail(email()));

    renderPage();
    await waitFor(() => expect(publicOption()).toBeChecked());
    await user.click(internalOption());

    expect(internalOption()).toBeChecked();
    expect(postButton()).toBeEnabled();
  });
});

// ---------------------------------------------------------------------------
// The audience preview follows the choice
// ---------------------------------------------------------------------------
describe("detail — audience preview", () => {
  it("counts the inherited groups under Public", async () => {
    getEmailById.mockResolvedValue(detail(email()));

    renderPage();

    await waitFor(() =>
      expect(audience()).toHaveTextContent(/visible to the 2 groups listed above/i)
    );
  });

  it("switches to the narrower description under Internal", async () => {
    const user = userEvent.setup();
    getEmailById.mockResolvedValue(detail(email()));

    renderPage();
    await waitFor(() => expect(publicOption()).toBeChecked());

    await user.click(internalOption());

    expect(audience()).toHaveTextContent(
      /only to the program chairs and this paper's authors/i
    );
    expect(audience()).toHaveTextContent(/narrower than the audience above/i);
  });

  it("⚠️ shows NO group ids under Internal — never a fabricated list", async () => {
    /* The safety property behind the preview design. The internal audience is
       built server-side from OPENREVIEW_VENUE_ID, which never reaches the
       browser, so any group id rendered here would be a guess — and it would
       appear in the same monospace pills as the REAL fetched ids above, reading
       as equally authoritative. The venue string is present elsewhere on the
       page, so this asserts on the preview element specifically. */
    const user = userEvent.setup();
    getEmailById.mockResolvedValue(detail(email()));

    renderPage();
    await waitFor(() => expect(publicOption()).toBeChecked());
    await user.click(internalOption());

    expect(audience().textContent).not.toMatch(/AAAI\.org/);
    expect(audience().textContent).not.toMatch(/Program_Chairs/);
    expect(audience().textContent).not.toMatch(/Submission\d/);
  });

  it("drops the count under Public when the audience is unknown", async () => {
    /* A failed lookup means the reply still inherits the parent's audience — we
       simply cannot say how many groups that is. Stated without a number rather
       than with a made-up one. */
    getEmailById.mockResolvedValue(
      detail(
        withReaders({
          state: "failed",
          readers: null,
          note_id: NOTE_ID,
          error: "boom",
          error_type: "OpenReviewAPIError",
        })
      )
    );

    renderPage();

    await waitFor(() =>
      expect(audience()).toHaveTextContent(
        /inherit the original comment's audience\.$/i
      )
    );
    expect(audience().textContent).not.toMatch(/\d+ group/);
  });

  it("says so when the inherited audience is genuinely empty", async () => {
    getEmailById.mockResolvedValue(
      detail(
        withReaders({
          state: "fetched",
          readers: [],
          note_id: NOTE_ID,
          error: null,
          error_type: null,
        })
      )
    );

    renderPage();

    await waitFor(() =>
      expect(audience()).toHaveTextContent(/which lists no groups/i)
    );
  });

  it("uses the singular for exactly one group", async () => {
    getEmailById.mockResolvedValue(
      detail(
        withReaders({
          state: "fetched",
          readers: [`${VENUE}/Program_Chairs`],
          note_id: NOTE_ID,
          error: null,
          error_type: null,
        })
      )
    );

    renderPage();

    await waitFor(() =>
      expect(audience()).toHaveTextContent(/visible to the 1 group listed above/i)
    );
  });
});

// ---------------------------------------------------------------------------
// Approve & Post — what gets sent
// ---------------------------------------------------------------------------
describe("post — the request", () => {
  it("sends the reply text, submission number and visibility", async () => {
    const user = userEvent.setup();
    getEmailById.mockResolvedValue(detail(email()));
    postOpenReviewReply.mockResolvedValue(posted());

    renderPage("7");
    await waitFor(() => expect(editor()).toHaveValue(REPLY));

    await user.click(postButton());

    await waitFor(() =>
      expect(postOpenReviewReply).toHaveBeenCalledWith("7", {
        reply_text: REPLY,
        submission_number: 1030,
        visibility: "public",
      })
    );
  });

  it("⚠️ sends the EDITED text, never the originally extracted string", async () => {
    /* The frontend twin of the backend's own guard. `extracted_reply_text` is
       still on the email and is still a plausible-looking string to read, so
       wiring it here would produce a request that succeeds and posts something
       the chair never approved — invisible in review, and public once sent. */
    const user = userEvent.setup();
    getEmailById.mockResolvedValue(detail(email()));
    postOpenReviewReply.mockResolvedValue(posted());

    renderPage();
    await waitFor(() => expect(editor()).toHaveValue(REPLY));

    await user.clear(editor());
    await user.type(editor(), "Rewritten by the chair before posting.");
    await user.click(postButton());

    await waitFor(() => expect(postOpenReviewReply).toHaveBeenCalled());
    const [, payload] = postOpenReviewReply.mock.calls[0];
    expect(payload.reply_text).toBe("Rewritten by the chair before posting.");
    expect(payload.reply_text).not.toBe(REPLY);
  });

  it("sends the SELECTED visibility, not always the default", async () => {
    const user = userEvent.setup();
    getEmailById.mockResolvedValue(detail(email()));
    postOpenReviewReply.mockResolvedValue(posted());

    renderPage();
    await waitFor(() => expect(publicOption()).toBeChecked());

    await user.click(internalOption());
    await user.click(postButton());

    await waitFor(() => expect(postOpenReviewReply).toHaveBeenCalled());
    expect(postOpenReviewReply.mock.calls[0][1].visibility).toBe("internal");
  });

  it("disables the button while the post is in flight", async () => {
    const user = userEvent.setup();
    getEmailById.mockResolvedValue(detail(email()));
    let release: (v: unknown) => void = () => {};
    postOpenReviewReply.mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      })
    );

    renderPage();
    await waitFor(() => expect(editor()).toHaveValue(REPLY));

    await user.click(postButton());

    // The label becomes "Posting…" while in flight, so the button must be found
    // by its new name — `postButton()` would throw here, which is itself the
    // evidence that the in-flight state is rendering.
    const inFlight = screen.getByRole("button", { name: /posting/i });
    expect(inFlight).toBeDisabled();
    expect(inFlight).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByRole("button", { name: /approve & post/i })).toBeNull();
    release(posted());
  });

  it("a double-click posts only ONCE", async () => {
    /* The comment is public and cannot be un-posted, so a second request is not
       a harmless duplicate. The in-flight disable is the structural guard; the
       backend's idempotency gate is the backstop for a revisit, not for this. */
    const user = userEvent.setup();
    getEmailById.mockResolvedValue(detail(email()));
    let release: (v: unknown) => void = () => {};
    postOpenReviewReply.mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      })
    );

    renderPage();
    await waitFor(() => expect(editor()).toHaveValue(REPLY));

    const button = postButton();
    await user.click(button);
    await user.click(button);
    await user.click(button);

    expect(postOpenReviewReply).toHaveBeenCalledTimes(1);
    release(posted());
  });

  it("refuses to submit an empty reply, and says why", async () => {
    const user = userEvent.setup();
    getEmailById.mockResolvedValue(
      detail(
        email({
          extraction: { ...email().extraction!, extracted_reply_text: "" },
        })
      )
    );

    renderPage();
    await waitFor(() => expect(editor()).toHaveValue(""));

    expect(postButton()).toBeDisabled();
    expect(screen.getByText(/write the reply before posting it/i)).toBeInTheDocument();

    await user.click(postButton());
    expect(postOpenReviewReply).not.toHaveBeenCalled();
  });

  it("refuses to submit with no submission number, and says why", async () => {
    /* The endpoint requires `submission_number` (gt=0) to build the invitation.
       Submitting without one would be a 422 the chair cannot act on, so the
       reason is stated instead. */
    getEmailById.mockResolvedValue(
      detail(
        email({
          extraction: { ...email().extraction!, submission_numbers: [] },
        })
      )
    );

    renderPage();
    await waitFor(() => expect(editor()).toHaveValue(REPLY));

    expect(postButton()).toBeDisabled();
    expect(
      screen.getByText(/no submission number was extracted/i)
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Approve & Post — success
// ---------------------------------------------------------------------------
describe("post — success", () => {
  it("confirms what was posted and links to it", async () => {
    const user = userEvent.setup();
    getEmailById.mockResolvedValue(detail(email()));
    postOpenReviewReply.mockResolvedValue(posted("solved"));

    renderPage();
    await waitFor(() => expect(editor()).toHaveValue(REPLY));
    await user.click(postButton());

    expect(
      await screen.findByText(/reply posted to openreview/i)
    ).toBeInTheDocument();
    expect(screen.getByText(/posted on submission 1030/i)).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /view the posted comment on openreview/i })
    ).toHaveAttribute(
      "href",
      `https://openreview.net/forum?id=${FORUM_ID}&noteId=${NOTE_ID}`
    );
  });

  it("retires the editor and the button once posted", async () => {
    /* The comment is live and cannot be un-posted, so leaving an editable box
       and a working button would invite posting text that no longer matches
       what is actually on the forum. */
    const user = userEvent.setup();
    getEmailById.mockResolvedValue(detail(email()));
    postOpenReviewReply.mockResolvedValue(posted("solved"));

    renderPage();
    await waitFor(() => expect(editor()).toHaveValue(REPLY));
    await user.click(postButton());

    await screen.findByText(/reply posted to openreview/i);
    expect(screen.queryByRole("textbox", { name: /reply to post/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /approve & post/i })).toBeNull();
  });

  it("names the restricted audience when posted internally", async () => {
    const user = userEvent.setup();
    getEmailById.mockResolvedValue(detail(email()));
    postOpenReviewReply.mockResolvedValue(
      posted("solved", { openreview_post: { visibility: "internal" } })
    );

    renderPage();
    await waitFor(() => expect(publicOption()).toBeChecked());
    await user.click(internalOption());
    await user.click(postButton());

    expect(
      await screen.findByText(/as a restricted comment/i)
    ).toBeInTheDocument();
  });

  it.each(["skipped_no_ticket", "skipped_closed"] as const)(
    "treats %s as a clean success, with no warning",
    async (outcome) => {
      /* ⚠️ THE TRAP THIS PINS: the backend sets the top-level `warning` for
         EVERY non-`solved` outcome, so a UI keyed on `warning != null` raises
         an alarm about "this email has no Zendesk ticket". Only `solve_failed`
         is a problem. */
      const user = userEvent.setup();
      getEmailById.mockResolvedValue(detail(email()));
      postOpenReviewReply.mockResolvedValue(posted(outcome));

      renderPage();
      await waitFor(() => expect(editor()).toHaveValue(REPLY));
      await user.click(postButton());

      await screen.findByText(/reply posted to openreview/i);
      expect(screen.queryByText(/needs attention/i)).toBeNull();
      expect(screen.queryByRole("alert")).toBeNull();
    }
  );

  it("flags solve_failed as needing attention (PLACEHOLDER — commit 16b)", async () => {
    /* ⚠️ PLACEHOLDER ASSERTION. This pins only that the state is SURFACED, not
       how it is handled — there is deliberately no recovery control yet. When
       16b lands, this test should grow, not be deleted. */
    const user = userEvent.setup();
    getEmailById.mockResolvedValue(detail(email()));
    postOpenReviewReply.mockResolvedValue(posted("solve_failed"));

    renderPage();
    await waitFor(() => expect(editor()).toHaveValue(REPLY));
    await user.click(postButton());

    expect(
      await screen.findByText(/reply posted to openreview/i)
    ).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      /zendesk ticket couldn't be closed automatically/i
    );
  });
});

// ---------------------------------------------------------------------------
// Approve & Post — failure
//
// Every case here means NOTHING was posted, so the page must keep the chair
// where they are, with the editor intact and a sentence specific enough to act
// on. A generic "something went wrong" is the failure mode being guarded.
// ---------------------------------------------------------------------------
describe("post — failure", () => {
  it.each([
    [
      "OpenReviewNoteNotFoundError",
      502,
      /no longer exists on openreview/i,
    ],
    [
      "OpenReviewPermissionError",
      502,
      /isn't allowed to comment on this submission/i,
    ],
    [
      "OpenReviewThreadMismatchError",
      502,
      /belongs to a different submission/i,
    ],
    ["OpenReviewAPIError", 502, /openreview rejected the request/i],
  ])("shows a specific message for %s", async (errorType, status, expected) => {
    const user = userEvent.setup();
    getEmailById.mockResolvedValue(detail(email()));
    postOpenReviewReply.mockRejectedValue(
      apiError(status, {
        message: "Posting the reply to OpenReview failed",
        error_type: errorType,
        error: "boom",
      })
    );

    renderPage();
    await waitFor(() => expect(editor()).toHaveValue(REPLY));
    await user.click(postButton());

    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent(expected);
    // Distinct per type, not one message with a code appended.
    expect(banner).not.toHaveTextContent(/something went wrong/i);
  });

  it("keeps the chair on the page with the edited text intact", async () => {
    const user = userEvent.setup();
    getEmailById.mockResolvedValue(detail(email()));
    postOpenReviewReply.mockRejectedValue(
      apiError(502, {
        message: "failed",
        error_type: "OpenReviewAPIError",
        error: "upstream 500",
      })
    );

    renderPage();
    await waitFor(() => expect(editor()).toHaveValue(REPLY));
    await user.clear(editor());
    await user.type(editor(), "Edited then failed.");
    await user.click(postButton());

    await screen.findByRole("alert");
    expect(editor()).toHaveValue("Edited then failed.");
    expect(postButton()).toBeEnabled();
    expect(screen.queryByText(/reply posted to openreview/i)).toBeNull();
  });

  it("surfaces the gate's own reason for a non-idempotency refusal", async () => {
    const user = userEvent.setup();
    getEmailById.mockResolvedValue(detail(email()));
    postOpenReviewReply.mockRejectedValue(
      apiError(409, {
        message: "Refused by the OpenReview post gate.",
        reason:
          "This email is not an OpenReview reply candidate " +
          "(openreview_reply_candidate is false), so there is no parent " +
          "comment to reply to.",
      })
    );

    renderPage();
    await waitFor(() => expect(editor()).toHaveValue(REPLY));
    await user.click(postButton());

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /not an openreview reply candidate/i
    );
  });

  it("explains a 501 as configuration, not something to retry", async () => {
    const user = userEvent.setup();
    getEmailById.mockResolvedValue(detail(email()));
    postOpenReviewReply.mockRejectedValue(
      apiError(501, {
        message: "OpenReview access is not configured.",
        error: "OPENREVIEW_USERNAME to be set",
      })
    );

    renderPage();
    await waitFor(() => expect(editor()).toHaveValue(REPLY));
    await user.click(postButton());

    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent(/isn't configured to post to openreview/i);
    expect(banner).toHaveTextContent(/not a retry/i);
  });

  it("handles a plain-string 404 detail without rendering JSON at a chair", async () => {
    /* ⚠️ The 404's `detail` is a STRING while every other error's is an object.
       Reading `.message` off it yields undefined, and a naive fallback prints
       the stringified body. */
    const user = userEvent.setup();
    getEmailById.mockResolvedValue(detail(email()));
    postOpenReviewReply.mockRejectedValue({
      status: 404,
      detail: "Email 7 not found",
      data: "Email 7 not found",
    });

    renderPage();
    await waitFor(() => expect(editor()).toHaveValue(REPLY));
    await user.click(postButton());

    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent("Email 7 not found");
    expect(banner.textContent).not.toMatch(/[{}]/);
  });
});

// ---------------------------------------------------------------------------
// Already posted — not an error
// ---------------------------------------------------------------------------
describe("post — already posted", () => {
  it("reports it as done, not as a failure", async () => {
    /* The chair asked for something that is already true. A red banner would
       say "your action failed" about an action that in fact succeeded — just
       not now. */
    const user = userEvent.setup();
    getEmailById.mockResolvedValue(detail(email()));
    postOpenReviewReply.mockRejectedValue(
      apiError(409, {
        message: "Refused by the OpenReview post gate.",
        reason:
          "This reply has already been posted to OpenReview (note abc123). " +
          "Refusing to post a duplicate.",
      })
    );

    renderPage();
    await waitFor(() => expect(editor()).toHaveValue(REPLY));
    await user.click(postButton());

    expect(
      await screen.findByText(/already on openreview/i)
    ).toBeInTheDocument();
    // Announced as status, never as an alert — that is the whole distinction.
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText(/refusing to post a duplicate/i)).toBeNull();
  });

  it("does not mistake another 409 for an already-posted one", async () => {
    const user = userEvent.setup();
    getEmailById.mockResolvedValue(detail(email()));
    postOpenReviewReply.mockRejectedValue(
      apiError(409, {
        message: "Refused by the OpenReview post gate.",
        reason: "The reply text is empty; there is nothing to post.",
      })
    );

    renderPage();
    await waitFor(() => expect(editor()).toHaveValue(REPLY));
    await user.click(postButton());

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /the reply text is empty/i
    );
    expect(screen.queryByText(/already on openreview/i)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// No actions are wired
// ---------------------------------------------------------------------------
// INVERTED from "detail — actions are not wired yet". Commit 16 landed the post
// action, so the old assertions (no action button; a note saying edits are not
// saved) are now the bug rather than the guard. What carries over is the
// SCOPE: exactly one action lives here, and the actions that belong to the main
// queue must not drift onto this page.
describe("detail — exactly one action", () => {
  it("offers Approve & Post, and nothing else", async () => {
    getEmailById.mockResolvedValue(detail(email()));

    renderPage();
    await waitFor(() => expect(editor()).toHaveValue(REPLY));

    expect(postButton()).toBeInTheDocument();
    // The queue's own actions are deliberately absent: this page relays a reply
    // to OpenReview, it does not triage the ticket.
    for (const name of [/reroute/i, /reassign/i, /re-draft/i, /mark solved/i]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
  });

  it("warns that posting cannot be undone, before it is clicked", async () => {
    /* Replaces the old "edits are not saved" note. The action publishes to a
       public venue and there is no un-post anywhere in this app, so the warning
       has to precede the click rather than explain it afterwards. */
    getEmailById.mockResolvedValue(detail(email()));

    renderPage();

    expect(
      await screen.findByText(/posting publishes this comment on openreview/i)
    ).toBeInTheDocument();
    expect(screen.getByText(/can't be undone here/i)).toBeInTheDocument();
  });
});
