/**
 * OpenReview reply detail — loading / not-found / error states.
 *
 * Split from `page.test.tsx` and stubbing the HOOK rather than the API layer,
 * mirroring `tickets/[ticketId]/page.error-states.test.tsx`. That split is not
 * stylistic: the normalized `ApiError` is a plain `{detail, status}` object, and
 * rejecting the API mock with one surfaces as an UNHANDLED REJECTION rather than
 * a query error — and a pending query left unsettled while the hook's 15s
 * refetchInterval holds a timer crashes the vitest worker outright. Driving the
 * hook's return value directly expresses each state exactly and deterministically.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { ApiError } from "@/types";

const state = vi.hoisted(() => ({
  email: null as unknown,
  isLoading: false,
  isError: false,
  error: null as ApiError | null,
  refetch: vi.fn(),
}));
/** Idle mutation. Every state in this file renders BEFORE an email exists, so
 *  the post action is unreachable here — but the barrel is replaced wholesale,
 *  so the hook still has to be defined or the page throws on render. */
const idlePost = vi.hoisted(() => ({
  mutate: vi.fn(),
  isPending: false,
  isError: false,
  isSuccess: false,
  error: null,
  data: undefined,
}));
vi.mock("@/hooks", () => ({
  useEmailById: () => state,
  usePostOpenReviewReply: () => idlePost,
}));

import OpenReviewReplyDetailPage from "./page";

const renderPage = (id = "7") =>
  render(<OpenReviewReplyDetailPage params={{ id }} />);

beforeEach(() => {
  state.email = null;
  state.isLoading = false;
  state.isError = false;
  state.error = null;
  state.refetch = vi.fn();
});

describe("detail — states", () => {
  it("shows a spinner while loading", () => {
    state.isLoading = true;

    renderPage();

    // Asserted by ROLE, not by the spinner's markup: LoadingSpinner is a
    // <span role="status" aria-label="Loading">, and pinning its classes would
    // couple this test to how the spinner is drawn rather than to what it means.
    expect(screen.getByRole("status", { name: /loading/i })).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("shows a not-found state for an unknown id", () => {
    state.isError = true;
    state.error = { detail: "Email 999 not found", status: 404 };

    renderPage("999");

    expect(screen.getByText("That reply doesn't exist")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("treats a non-numeric id as not-found, not a crash", () => {
    /* The backend coerces the path segment and 404s an uncoercible one, so
       unlike /tickets/[ticketId] there is no 422 case to fold in. */
    state.isError = true;
    state.error = { detail: "Email abc not found", status: 404 };

    renderPage("abc");

    expect(screen.getByText("That reply doesn't exist")).toBeInTheDocument();
  });

  it("distinguishes an unexpected failure from not-found", () => {
    state.isError = true;
    state.error = { detail: "boom", status: 500 };

    renderPage();

    expect(screen.getByText("Couldn't load this reply.")).toBeInTheDocument();
    expect(screen.queryByText("That reply doesn't exist")).toBeNull();
  });

  it("offers a retry that refetches on an unexpected failure", async () => {
    const user = userEvent.setup();
    state.isError = true;
    state.error = { detail: "boom", status: 500 };

    renderPage();
    await user.click(screen.getByRole("button", { name: /retry/i }));

    expect(state.refetch).toHaveBeenCalledTimes(1);
  });

  it("offers no retry on not-found (retrying cannot help)", () => {
    state.isError = true;
    state.error = { detail: "nope", status: 404 };

    renderPage();

    expect(screen.queryByRole("button", { name: /retry/i })).toBeNull();
  });

  it("keeps the back link available in every state", () => {
    state.isError = true;
    state.error = { detail: "nope", status: 404 };

    renderPage();

    expect(
      screen.getByRole("link", { name: /back to openreview replies/i })
    ).toBeInTheDocument();
  });
});
