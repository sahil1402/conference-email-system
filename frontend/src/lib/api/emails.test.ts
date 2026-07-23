/**
 * Unit tests for getEmailByTicketId (Piece C1).
 *
 * The shared axios instance (./client) is mocked so we exercise ONLY this
 * function's URL + return-parsing and its error propagation. The real client
 * interceptor already normalizes non-2xx into an ApiError and rejects; sibling
 * functions here simply await the client and let that rejection propagate, so
 * the 404 test asserts the same: the normalized ApiError surfaces unswallowed.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

import type { ApiError, EmailDetailResponse } from "@/types";

const getMock = vi.hoisted(() => vi.fn());
vi.mock("./client", () => ({ default: { get: getMock } }));

import { getEmailByTicketId } from "./emails";

const RESPONSE = {
  email: { id: 7, zendesk_ticket_id: 21567, source: "zendesk" },
  audit_trail: [
    {
      id: 1,
      email_id: "7",
      action: "classified",
      actor: "pipeline",
      timestamp: "2026-01-01T00:00:00Z",
      metadata: {},
    },
  ],
} as unknown as EmailDetailResponse;

describe("getEmailByTicketId", () => {
  beforeEach(() => getMock.mockReset());

  it("fetches by ticket id and returns the parsed { email, audit_trail }", async () => {
    getMock.mockResolvedValue({ data: RESPONSE });

    const result = await getEmailByTicketId(21567);

    expect(getMock).toHaveBeenCalledWith("/emails/by-ticket/21567");
    expect(result).toEqual(RESPONSE);
    expect(result.email.zendesk_ticket_id).toBe(21567);
    expect(result.audit_trail[0].action).toBe("classified");
  });

  it("accepts a string ticket id (builds the same path)", async () => {
    getMock.mockResolvedValue({ data: RESPONSE });

    await getEmailByTicketId("21567");

    expect(getMock).toHaveBeenCalledWith("/emails/by-ticket/21567");
  });

  it("propagates the normalized ApiError on 404 (same as sibling fns)", async () => {
    const err: ApiError = {
      detail: "No email found for ticket id 999",
      status: 404,
    };
    getMock.mockImplementationOnce(() => Promise.reject(err));

    // The function must not swallow the rejection — it surfaces the normalized
    // ApiError (status + detail) exactly as the shared interceptor produced it.
    await expect(getEmailByTicketId(999)).rejects.toMatchObject({
      status: 404,
      detail: err.detail,
    });
  });
});
