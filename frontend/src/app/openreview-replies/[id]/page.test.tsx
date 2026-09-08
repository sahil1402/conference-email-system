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
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import type { Email, EmailDetailResponse } from "@/types";

const getEmailById = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, getEmailById };
});

import OpenReviewReplyDetailPage from "./page";

const FORUM_ID = "ll0avn6ylq";
const NOTE_ID = "jnHgRMHgrm";
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
    ...overrides,
  } as unknown as Email;
}

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

beforeEach(() => getEmailById.mockReset());

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
// No actions are wired
// ---------------------------------------------------------------------------
describe("detail — actions are not wired yet", () => {
  it("renders no post/approve/reroute control", async () => {
    /* ⚠️ DELETE/INVERT when the actions land. The page renders NO action button
       at all rather than disabled ones, so a chair cannot mistake a dead
       control for a broken one. */
    getEmailById.mockResolvedValue(detail(email()));

    renderPage();
    await waitFor(() => expect(editor()).toHaveValue(REPLY));

    for (const name of [/post/i, /approve/i, /reroute/i, /send/i, /submit/i]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
  });

  it("tells the chair in the UI that edits are not saved", async () => {
    /* Stated on screen, not only in a code comment — someone who types here and
       finds no submit needs to know that is expected. */
    getEmailById.mockResolvedValue(detail(email()));

    renderPage();

    expect(
      await screen.findByText(/isn't wired up yet — edits here are not saved/i)
    ).toBeInTheDocument();
  });
});
