/**
 * SubmissionDetails (2a) — the panel's render / don't-render contract, plus
 * submission_number output.
 *
 * SCOPE LIMIT: submission_number (2a) and the OpenReview link (2b) have UI.
 * `authors` does NOT yet — the emptiness check already counts it, so the
 * "renders on authors alone" case below asserts only that the CONTAINER
 * appears, deliberately not any author output, which is 2c's job. That test is
 * what stops 2c from having to revisit this component's render decision.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { SubmissionDetails } from "./SubmissionDetails";
import type { AuthorMention, ExtractionData } from "@/types";

const EMPTY: ExtractionData = {
  submission_number: null,
  openreview_forum_id: null,
  authors: [],
  method: "none",
};

function extraction(overrides: Partial<ExtractionData> = {}): ExtractionData {
  return { ...EMPTY, ...overrides };
}

function author(overrides: Partial<AuthorMention> = {}): AuthorMention {
  return { name: null, email: null, affiliation: null, ...overrides };
}

/** The panel container, or null when the component rendered nothing. */
function panel(): HTMLElement | null {
  return screen.queryByRole("group", { name: /submission details/i });
}

describe("SubmissionDetails — renders nothing", () => {
  it("renders nothing when extraction is null (row never examined)", () => {
    const { container } = render(<SubmissionDetails extraction={null} />);
    expect(container).toBeEmptyDOMElement();
    expect(panel()).toBeNull();
  });

  it("renders nothing when all three fields are empty (examined, found nothing)", () => {
    const { container } = render(<SubmissionDetails extraction={extraction()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when the fields are present but blank", () => {
    const { container } = render(
      <SubmissionDetails
        extraction={extraction({
          submission_number: "   ",
          openreview_forum_id: "",
          authors: [author({ name: "  " })],
        })}
      />
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing for an authors array of entirely empty mentions", () => {
    const { container } = render(
      <SubmissionDetails extraction={extraction({ authors: [author(), author()] })} />
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing regardless of method when every field is empty", () => {
    // An examined-but-empty result still has nothing to show.
    const { container } = render(
      <SubmissionDetails extraction={extraction({ method: "llm_distiller" })} />
    );
    expect(container).toBeEmptyDOMElement();
  });
});

describe("SubmissionDetails — submission number", () => {
  it("renders the title and the number", () => {
    render(
      <SubmissionDetails extraction={extraction({ submission_number: "22336" })} />
    );
    expect(screen.getByText("Submission Details")).toBeInTheDocument();
    expect(screen.getByText("Number")).toBeInTheDocument();
    expect(screen.getByText("22336")).toBeInTheDocument();
  });

  it("exposes the panel as a labelled group", () => {
    render(
      <SubmissionDetails extraction={extraction({ submission_number: "22336" })} />
    );
    expect(panel()).toBeInTheDocument();
  });

  it("renders the number verbatim, without adding a # prefix", () => {
    // The ticket badge elsewhere prefixes '#'; a submission number is a
    // different identifier and must not be dressed up as one.
    render(
      <SubmissionDetails extraction={extraction({ submission_number: "9904" })} />
    );
    expect(screen.getByText("9904")).toBeInTheDocument();
    expect(screen.queryByText("#9904")).toBeNull();
  });

  it("renders a non-numeric number as given", () => {
    // The backend passes identifiers through unvalidated; the UI must not
    // silently drop one it did not expect.
    render(
      <SubmissionDetails extraction={extraction({ submission_number: "AAAI-2026" })} />
    );
    expect(screen.getByText("AAAI-2026")).toBeInTheDocument();
  });

  it("accepts an extra className on the container", () => {
    render(
      <SubmissionDetails
        extraction={extraction({ submission_number: "22336" })}
        className="mt-4"
      />
    );
    expect(panel()).toHaveClass("mt-4");
  });
});

describe("SubmissionDetails — OpenReview link", () => {
  const FORUM_ID = "Ab3xY9kLm2";

  /** The link, queried by its accessible name rather than its opaque text. */
  function forumLink(): HTMLElement {
    return screen.getByRole("link", { name: /openreview/i });
  }

  it("renders the link when only openreview_forum_id is present", () => {
    render(
      <SubmissionDetails extraction={extraction({ openreview_forum_id: FORUM_ID })} />
    );
    expect(panel()).toBeInTheDocument();
    expect(screen.getByText("OpenReview")).toBeInTheDocument();
    expect(forumLink()).toBeInTheDocument();
    // No number row, since there is no number.
    expect(screen.queryByText("Number")).toBeNull();
  });

  it("points at the public forum page for that id", () => {
    render(
      <SubmissionDetails extraction={extraction({ openreview_forum_id: FORUM_ID })} />
    );
    expect(forumLink()).toHaveAttribute(
      "href",
      "https://openreview.net/forum?id=Ab3xY9kLm2"
    );
  });

  it("opens in a new tab safely", () => {
    render(
      <SubmissionDetails extraction={extraction({ openreview_forum_id: FORUM_ID })} />
    );
    const link = forumLink();
    expect(link).toHaveAttribute("target", "_blank");
    // noopener is the security-relevant half — without it the opened page gets
    // a handle on window.opener.
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("names the destination and the new tab in its accessible label", () => {
    // Matches ZendeskLinkButton's "(opens in new tab)" convention; the raw id
    // alone would tell a screen-reader user nothing.
    render(
      <SubmissionDetails extraction={extraction({ openreview_forum_id: FORUM_ID })} />
    );
    expect(forumLink()).toHaveAccessibleName(
      `Open submission ${FORUM_ID} in OpenReview (opens in new tab)`
    );
  });

  it("shows the forum id as the link text", () => {
    render(
      <SubmissionDetails extraction={extraction({ openreview_forum_id: FORUM_ID })} />
    );
    expect(forumLink()).toHaveTextContent(FORUM_ID);
  });

  it("keeps the visible text inside the accessible name (WCAG label-in-name)", () => {
    render(
      <SubmissionDetails extraction={extraction({ openreview_forum_id: FORUM_ID })} />
    );
    const link = forumLink();
    expect(link.getAttribute("aria-label")).toContain(link.textContent?.trim());
  });

  it("trims surrounding whitespace out of the href", () => {
    // The backend passes identifiers through unvalidated; untrimmed whitespace
    // would become %20 in the URL and break the link.
    render(
      <SubmissionDetails
        extraction={extraction({ openreview_forum_id: `  ${FORUM_ID}  ` })}
      />
    );
    expect(forumLink()).toHaveAttribute(
      "href",
      "https://openreview.net/forum?id=Ab3xY9kLm2"
    );
  });

  it("escapes an id carrying URL-special characters", () => {
    render(
      <SubmissionDetails extraction={extraction({ openreview_forum_id: "a&b=c d" })} />
    );
    expect(forumLink()).toHaveAttribute(
      "href",
      "https://openreview.net/forum?id=a%26b%3Dc%20d"
    );
  });

  it("renders no link when there is no forum id", () => {
    render(
      <SubmissionDetails extraction={extraction({ submission_number: "22336" })} />
    );
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.queryByText("OpenReview")).toBeNull();
  });

  it("renders both rows together, number first", () => {
    render(
      <SubmissionDetails
        extraction={extraction({
          submission_number: "22336",
          openreview_forum_id: FORUM_ID,
        })}
      />
    );
    const group = panel()!;
    expect(screen.getByText("22336")).toBeInTheDocument();
    expect(forumLink()).toBeInTheDocument();
    // Reading order: the number row precedes the OpenReview row.
    const labels = Array.from(group.querySelectorAll("span"))
      .map((el) => el.textContent)
      .filter((text) => text === "Number" || text === "OpenReview");
    expect(labels).toEqual(["Number", "OpenReview"]);
  });

  it("aligns both rows in one label/value grid", () => {
    // A shared grid is what keeps the value column aligned as rows are added;
    // per-row flex containers would let the labels' differing widths stagger it.
    render(
      <SubmissionDetails
        extraction={extraction({
          submission_number: "22336",
          openreview_forum_id: FORUM_ID,
        })}
      />
    );
    const grid = panel()!.querySelector(".grid");
    expect(grid).not.toBeNull();
    expect(grid).toContainElement(screen.getByText("Number"));
    expect(grid).toContainElement(screen.getByText("OpenReview"));
  });
});

describe("SubmissionDetails — other fields keep the panel alive (2b/2c)", () => {
  it("still renders when only authors are present", () => {
    render(
      <SubmissionDetails
        extraction={extraction({ authors: [author({ name: "Jane Roe" })] })}
      />
    );
    expect(panel()).toBeInTheDocument();
    expect(screen.queryByText("Number")).toBeNull();
  });

  it("treats a mention with only an email or only an affiliation as present", () => {
    for (const only of [author({ email: "jane@example.edu" }), author({ affiliation: "Example University" })]) {
      const { unmount } = render(
        <SubmissionDetails extraction={extraction({ authors: [only] })} />
      );
      expect(panel()).toBeInTheDocument();
      unmount();
    }
  });
});
