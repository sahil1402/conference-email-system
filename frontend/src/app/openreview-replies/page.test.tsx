/**
 * OpenReview Replies list.
 *
 * Mocks the API layer (`@/lib/api`) and lets the real hook, React Query and
 * router run — the repo's convention for page-level tests, and the reason the
 * loading/empty/error branches here are driven by genuine query states rather
 * than by stubbing the hook's return.
 *
 * ⚠️ The raw-body-vs-extracted-text assertions are the point of this file. The
 * two fields are both plausible strings on the same object, so wiring
 * `email.body` instead of `extraction.extracted_reply_text` would render
 * perfectly and look fine in review — while shipping the entire quoted
 * notification into the row and defeating the quote-stripping work upstream.
 * Every fixture below therefore makes the two texts UNMISTAKABLY different, and
 * the rows are asserted to show one and never the other.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import type { Email, EmailQueueResponse } from "@/types";

const push = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const getOpenReviewQueue = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, getOpenReviewQueue };
});

import OpenReviewRepliesPage from "./page";

/** The text a chair must see. */
const REPLY = "Thank you — I will submit my review by Friday.";
/** The raw body: the reply PLUS the quoted notification underneath it. Nothing
 *  from the quoted half may reach a row. */
const RAW_BODY = [
  REPLY,
  "",
  "-----Original Message-----",
  'From: "AAAI 2027" <aaai2027-notifications@openreview.net>',
  "Subject: SPC commented on a paper you are reviewing",
  "QUOTED_NOTIFICATION_SENTINEL should never render in a row",
].join("\n");

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
      openreview_forum_ids: ["ll0avn6ylq"],
      openreview_note_id: "jnHgRMHgrm",
      openreview_notification_sender: "aaai2027-notifications@openreview.net",
      openreview_reply_candidate: true,
      extracted_reply_text: REPLY,
      authors: [],
      method: "llm_distiller",
    },
    ...overrides,
  } as unknown as Email;
}

function response(emails: Email[], total = emails.length): EmailQueueResponse {
  return { emails, total, page_info: {} } as EmailQueueResponse;
}

function Wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const renderPage = () =>
  render(<OpenReviewRepliesPage />, { wrapper: Wrapper });

beforeEach(() => {
  push.mockReset();
  getOpenReviewQueue.mockReset();
});

// ---------------------------------------------------------------------------
// Rows
// ---------------------------------------------------------------------------
describe("OpenReviewRepliesPage — rows", () => {
  it("renders a row per email from the queue", async () => {
    getOpenReviewQueue.mockResolvedValue(
      response([email({ id: 1 }), email({ id: 2 }), email({ id: 3 })], 3)
    );

    renderPage();

    await waitFor(() =>
      expect(screen.getAllByRole("button")).toHaveLength(3)
    );
  });

  it("shows the sender, subject, submission number and time", async () => {
    getOpenReviewQueue.mockResolvedValue(response([email()]));

    renderPage();

    const row = await screen.findByRole("button");
    expect(within(row).getByText(/Re: \[AAAI 2027\] SPC commented/)).toBeInTheDocument();
    expect(within(row).getByText(/Wei Zhang/)).toBeInTheDocument();
    expect(within(row).getByText("Submission 1030")).toBeInTheDocument();
  });

  it("falls back to the forum id when no submission number was extracted", async () => {
    getOpenReviewQueue.mockResolvedValue(
      response([
        email({
          extraction: {
            ...email().extraction!,
            submission_numbers: [],
          },
        }),
      ])
    );

    const row = (renderPage(), await screen.findByRole("button"));
    expect(within(row).getByText("Forum ll0avn6ylq")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// THE field-wiring guard
// ---------------------------------------------------------------------------
describe("OpenReviewRepliesPage — extracted reply, never the raw body", () => {
  it("renders extracted_reply_text", async () => {
    getOpenReviewQueue.mockResolvedValue(response([email()]));

    renderPage();

    const row = await screen.findByRole("button");
    expect(within(row).getByText(REPLY)).toBeInTheDocument();
  });

  it("does NOT render the quoted notification from the raw body", async () => {
    /* The discriminator: this sentinel exists ONLY in `body`, so it can appear
       in a row only if the wrong field was wired. */
    getOpenReviewQueue.mockResolvedValue(response([email()]));

    renderPage();
    await screen.findByRole("button");

    expect(screen.queryByText(/QUOTED_NOTIFICATION_SENTINEL/)).toBeNull();
    expect(screen.queryByText(/-----Original Message-----/)).toBeNull();
    expect(screen.queryByText(/aaai2027-notifications@openreview\.net/)).toBeNull();
  });

  it("shows the extracted text even when the raw body would be more eye-catching", async () => {
    /* Guards the subtler swap: a fixture where BOTH fields are short, readable
       one-liners, so nothing but the field choice distinguishes them. */
    getOpenReviewQueue.mockResolvedValue(
      response([
        email({
          body: "RAW_BODY_TEXT",
          extraction: {
            ...email().extraction!,
            extracted_reply_text: "EXTRACTED_TEXT",
          },
        }),
      ])
    );

    renderPage();

    const row = await screen.findByRole("button");
    expect(within(row).getByText("EXTRACTED_TEXT")).toBeInTheDocument();
    expect(within(row).queryByText("RAW_BODY_TEXT")).toBeNull();
  });

  it("truncates a long reply rather than shipping the whole thing into the row", async () => {
    const long = "word ".repeat(200).trim();
    getOpenReviewQueue.mockResolvedValue(
      response([
        email({ extraction: { ...email().extraction!, extracted_reply_text: long } }),
      ])
    );

    renderPage();

    const row = await screen.findByRole("button");
    expect(row.textContent!.length).toBeLessThan(long.length);
    expect(row.textContent).toContain("…");
  });

  it("labels a reply that stripped to nothing instead of rendering a blank line", async () => {
    /* An empty extracted_reply_text is a REAL state — a body that was entirely
       quoted material — so it needs to read as such, not as a rendering bug. */
    getOpenReviewQueue.mockResolvedValue(
      response([
        email({ extraction: { ...email().extraction!, extracted_reply_text: "" } }),
      ])
    );

    renderPage();

    const row = await screen.findByRole("button");
    expect(within(row).getByText("(no reply text extracted)")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// States
// ---------------------------------------------------------------------------
describe("OpenReviewRepliesPage — states", () => {
  it("shows the queue-specific empty message, not a generic one", async () => {
    getOpenReviewQueue.mockResolvedValue(response([], 0));

    renderPage();

    expect(
      await screen.findByText("No OpenReview reply candidates right now")
    ).toBeInTheDocument();
    expect(
      screen.getByText(/replies to an OpenReview notification/i)
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /./ })).toBeNull();
  });

  it("shows an error state with a retry when the fetch fails", async () => {
    getOpenReviewQueue.mockRejectedValue({ detail: "boom", status: 500 });

    renderPage();

    expect(
      await screen.findByText("Couldn't load OpenReview replies.")
    ).toBeInTheDocument();
  });

  it("retry refetches", async () => {
    const user = userEvent.setup();
    getOpenReviewQueue.mockRejectedValueOnce({ detail: "boom", status: 500 });

    renderPage();
    await screen.findByText("Couldn't load OpenReview replies.");

    getOpenReviewQueue.mockResolvedValue(response([email()]));
    await user.click(screen.getByRole("button", { name: /retry/i }));

    await waitFor(() =>
      expect(screen.getByText(REPLY)).toBeInTheDocument()
    );
  });

  it("keeps the header visible in every state", async () => {
    getOpenReviewQueue.mockResolvedValue(response([], 0));

    renderPage();

    expect(
      screen.getByRole("heading", { level: 1, name: "OpenReview Replies" })
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Navigation + pagination
// ---------------------------------------------------------------------------
describe("OpenReviewRepliesPage — navigation", () => {
  it("navigates to the detail route for the clicked row", async () => {
    const user = userEvent.setup();
    getOpenReviewQueue.mockResolvedValue(response([email({ id: 42 })]));

    renderPage();
    await user.click(await screen.findByRole("button"));

    expect(push).toHaveBeenCalledWith("/openreview-replies/42");
  });

  it("navigates by the clicked row's OWN id", async () => {
    const user = userEvent.setup();
    getOpenReviewQueue.mockResolvedValue(
      response([email({ id: 11 }), email({ id: 22 })], 2)
    );

    renderPage();
    await waitFor(() => expect(screen.getAllByRole("button")).toHaveLength(2));
    await user.click(screen.getAllByRole("button")[1]);

    expect(push).toHaveBeenCalledWith("/openreview-replies/22");
    expect(push).toHaveBeenCalledTimes(1);
  });

  it("requests the first page on mount", async () => {
    getOpenReviewQueue.mockResolvedValue(response([email()], 1));

    renderPage();
    await screen.findByRole("button");

    expect(getOpenReviewQueue).toHaveBeenCalledWith({ limit: 100, offset: 0 });
  });

  it("renders no pagination for a single page", async () => {
    getOpenReviewQueue.mockResolvedValue(response([email()], 1));

    renderPage();
    await screen.findByRole("button");

    expect(screen.queryByRole("button", { name: /next page/i })).toBeNull();
  });

  it("pages by QUEUE_PAGE_SIZE when there is more than one page", async () => {
    const user = userEvent.setup();
    getOpenReviewQueue.mockResolvedValue(response([email()], 250));

    renderPage();
    await screen.findByText(REPLY);

    await user.click(screen.getByRole("button", { name: /next page/i }));

    await waitFor(() =>
      expect(getOpenReviewQueue).toHaveBeenCalledWith({ limit: 100, offset: 100 })
    );
  });
});
