/**
 * Unit tests for getOpenReviewQueue.
 *
 * The shared axios instance (./client) is mocked so we exercise ONLY this
 * function's URL + params + return-parsing and its error propagation. The real
 * client interceptor already normalizes non-2xx into an ApiError and rejects;
 * sibling functions here simply await the client and let that rejection
 * propagate, so the error test asserts the same — matching the convention in
 * emails.test.ts.
 *
 * ⚠️ THE URL ASSERTIONS ARE THE POINT. This function is a near-copy of
 * getEmailQueue — same params, same return type — so the realistic failure is a
 * copy-paste that leaves it fetching "/emails/queue". That would be silently
 * WRONG rather than broken: the call succeeds, the types check, and the caller
 * renders the main queue's rows under an OpenReview heading. Hence a dedicated
 * test that the two functions hit DIFFERENT paths, not just that this one hits
 * a path.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

import type { ApiError, EmailQueueResponse } from "@/types";

const getMock = vi.hoisted(() => vi.fn());
vi.mock("./client", () => ({ default: { get: getMock } }));

import { getEmailQueue, getOpenReviewQueue } from "./emails";

const PATH = "/emails/queue/openreview";

const RESPONSE = {
  emails: [
    {
      id: 7,
      subject: "Re: [AAAI 2027] SPC commented on a paper",
      zendesk_ticket_id: 21567,
      source: "zendesk",
      extraction: {
        openreview_note_id: "jnHgRMHgrm",
        openreview_forum_ids: ["ll0avn6ylq"],
        openreview_reply_candidate: true,
        extracted_reply_text: "I will review before the deadline.",
      },
    },
  ],
  total: 1,
  page_info: { limit: 100, offset: 0, lane: null },
} as unknown as EmailQueueResponse;

describe("getOpenReviewQueue", () => {
  beforeEach(() => getMock.mockReset());

  it("returns the parsed { emails, total, page_info } envelope", async () => {
    getMock.mockResolvedValue({ data: RESPONSE });

    const result = await getOpenReviewQueue();

    expect(result).toEqual(RESPONSE);
    expect(result.total).toBe(1);
    expect(result.emails).toHaveLength(1);
    expect(result.emails[0].id).toBe(7);
  });

  it("calls the openreview queue path, NOT the main queue path", async () => {
    getMock.mockResolvedValue({ data: RESPONSE });

    await getOpenReviewQueue();

    expect(getMock).toHaveBeenCalledWith(PATH, { params: undefined });
    expect(getMock).not.toHaveBeenCalledWith(
      "/emails/queue",
      expect.anything()
    );
  });

  it("hits a DIFFERENT path from getEmailQueue", async () => {
    /* The copy-paste guard, stated as a relationship rather than a literal: if
       someone points this function at "/emails/queue" the two calls collapse
       onto the same URL and this fails, even if the literal above were edited
       to match the mistake. */
    getMock.mockResolvedValue({ data: RESPONSE });

    await getOpenReviewQueue();
    await getEmailQueue();

    const [orUrl] = getMock.mock.calls[0];
    const [mainUrl] = getMock.mock.calls[1];
    expect(orUrl).not.toBe(mainUrl);
    expect(orUrl).toBe(PATH);
    expect(mainUrl).toBe("/emails/queue");
  });

  it("forwards query params unchanged", async () => {
    getMock.mockResolvedValue({ data: RESPONSE });
    const params = {
      limit: 100,
      offset: 200,
      lane: "human_review",
      chair_id: 3,
      unassigned: false,
      status: "DRAFT_GENERATED",
      source: "zendesk",
      zendesk_status: "open",
      search: "#21567",
      received_after: "2026-01-01",
      received_before: "2026-01-31",
    };

    await getOpenReviewQueue(params);

    expect(getMock).toHaveBeenCalledWith(PATH, { params });
  });

  it("omits params entirely when none are given", async () => {
    getMock.mockResolvedValue({ data: RESPONSE });

    await getOpenReviewQueue();

    expect(getMock).toHaveBeenCalledWith(PATH, { params: undefined });
  });

  it("returns an empty envelope as-is (no coercion to a falsy default)", async () => {
    /* An empty OpenReview queue is the ORDINARY state — most inboxes have no
       pending relays — so the empty case must round-trip honestly rather than
       be smoothed into undefined/null by the client. */
    const empty = { emails: [], total: 0, page_info: {} } as EmailQueueResponse;
    getMock.mockResolvedValue({ data: empty });

    const result = await getOpenReviewQueue();

    expect(result).toEqual(empty);
    expect(result.emails).toEqual([]);
    expect(result.total).toBe(0);
  });

  it("propagates the normalized ApiError (same as sibling fns)", async () => {
    const err: ApiError = { detail: "Internal Server Error", status: 500 };
    getMock.mockImplementationOnce(() => Promise.reject(err));

    await expect(getOpenReviewQueue()).rejects.toEqual(err);
  });

  it("propagates a 422 from an inverted date range unswallowed", async () => {
    /* The backend shares its date-range parsing with /queue, so an inverted
       window is a 422 here too; the client must not absorb it into an empty
       page, which would look like "no OpenReview replies" instead of a bad
       filter. */
    const err: ApiError = {
      detail: "received_after must not be later than received_before",
      status: 422,
    };
    getMock.mockImplementationOnce(() => Promise.reject(err));

    await expect(
      getOpenReviewQueue({ received_after: "2026-02-01", received_before: "2026-01-01" })
    ).rejects.toEqual(err);
  });
});
