/**
 * OpenReview Replies route — stub page.
 *
 * Deliberately narrow: this commit adds a reachable route and nothing else, so
 * the only claims worth pinning are that it renders without crashing, carries
 * its heading, and matches the page shape every other route uses.
 *
 * SCOPE LIMIT: no data assertions, because the page fetches nothing yet. When
 * the list lands, its tests belong here beside these — the "renders no list"
 * assertion below is the one that will need deleting, and it says so.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import OpenReviewRepliesPage from "./page";

describe("OpenReviewRepliesPage (stub)", () => {
  it("renders without crashing", () => {
    expect(() => render(<OpenReviewRepliesPage />)).not.toThrow();
  });

  it("shows the page heading", () => {
    render(<OpenReviewRepliesPage />);

    expect(
      screen.getByRole("heading", { level: 1, name: "OpenReview Replies" })
    ).toBeInTheDocument();
  });

  it("uses the same page wrapper + header shape as the other routes", () => {
    /* AppShell is applied globally in app/layout.tsx, so a page owns only its
       own content — and every other page opens with this centred wrapper and a
       header block. Pinned so the follow-up adds a list UNDER an unchanged
       header rather than restyling the page. */
    const { container } = render(<OpenReviewRepliesPage />);

    const wrapper = container.firstElementChild;
    expect(wrapper?.className).toContain("mx-auto");
    expect(wrapper?.className).toContain("max-w-6xl");
    expect(container.querySelector("header")).not.toBeNull();
  });

  it("fetches nothing and renders no list yet", () => {
    /* ⚠️ DELETE THIS when the list lands — it pins the stub-ness of the stub,
       which is the whole scope of this commit, and is expected to fail the
       moment real content arrives. */
    const { container } = render(<OpenReviewRepliesPage />);

    expect(container.querySelectorAll("ul, ol, table")).toHaveLength(0);
  });
});
