/**
 * SubmissionDetails (2a) — the panel's render / don't-render contract, plus
 * submission_number output.
 *
 * All three fields now have UI: submission_number (2a), the OpenReview link
 * (2b) and authors (2c). The 2a/2b container-only placeholders for authors have
 * been replaced by real output assertions below.
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

  it("exposes the panel as a group named by its visible heading", () => {
    // The accessible name comes from the <h3> via aria-labelledby, not a
    // duplicated aria-label string — one source of truth, so the name shown on
    // screen and the name announced can never drift apart.
    render(
      <SubmissionDetails extraction={extraction({ submission_number: "22336" })} />
    );
    const group = panel()!;
    expect(group).toBeInTheDocument();
    expect(group).toHaveAccessibleName("Submission Details");

    const heading = screen.getByRole("heading", { name: "Submission Details" });
    expect(group.getAttribute("aria-labelledby")).toBe(heading.id);
    expect(heading.id).not.toBe("");
    // No competing label: aria-label would silently win over aria-labelledby.
    expect(group).not.toHaveAttribute("aria-label");
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

describe("SubmissionDetails — authors", () => {
  /** The authors cell (the grid's second column for that row). */
  function authorsCell(): HTMLElement {
    return screen.getByText("Authors").nextElementSibling as HTMLElement;
  }

  it("renders a fully populated mention: name, email, affiliation", () => {
    render(
      <SubmissionDetails
        extraction={extraction({
          authors: [
            author({
              name: "Jane Roe",
              email: "jane@example.edu",
              affiliation: "Example University",
            }),
          ],
        })}
      />
    );
    expect(screen.getByText("Authors")).toBeInTheDocument();
    expect(screen.getByText("Jane Roe")).toBeInTheDocument();
    expect(screen.getByText("jane@example.edu")).toBeInTheDocument();
    expect(screen.getByText("Example University")).toBeInTheDocument();
  });

  it("renders a name-only mention", () => {
    render(
      <SubmissionDetails
        extraction={extraction({ authors: [author({ name: "Jane Roe" })] })}
      />
    );
    expect(screen.getByText("Jane Roe")).toBeInTheDocument();
    expect(screen.queryByText("Number")).toBeNull();
  });

  it("renders an email-only mention", () => {
    render(
      <SubmissionDetails
        extraction={extraction({ authors: [author({ email: "jane@example.edu" })] })}
      />
    );
    expect(screen.getByText("jane@example.edu")).toBeInTheDocument();
  });

  it("renders an affiliation-only mention", () => {
    render(
      <SubmissionDetails
        extraction={extraction({
          authors: [author({ affiliation: "Example University" })],
        })}
      />
    );
    expect(screen.getByText("Example University")).toBeInTheDocument();
  });

  it("emits no stray separator for a partial mention", () => {
    // A missing field must contribute neither text nor a dangling middot.
    render(
      <SubmissionDetails
        extraction={extraction({ authors: [author({ name: "Jane Roe" })] })}
      />
    );
    const text = authorsCell().textContent ?? "";
    expect(text).toBe("Jane Roe");
    expect(text).not.toContain("·");
  });

  it("separates the parts of a full mention", () => {
    render(
      <SubmissionDetails
        extraction={extraction({
          authors: [
            author({
              name: "Jane Roe",
              email: "jane@example.edu",
              affiliation: "Example University",
            }),
          ],
        })}
      />
    );
    expect(authorsCell().textContent).toBe(
      "Jane Roe·jane@example.edu·Example University"
    );
  });

  it("renders nothing for a blank-string field rather than an empty fragment", () => {
    render(
      <SubmissionDetails
        extraction={extraction({
          authors: [author({ name: "Jane Roe", email: "   ", affiliation: "" })],
        })}
      />
    );
    expect(authorsCell().textContent).toBe("Jane Roe");
  });

  it("skips a BLANK leading field without a dangling separator", () => {
    // A blank `name` must not occupy the first slot — otherwise the email
    // becomes the second part and picks up a leading middot.
    render(
      <SubmissionDetails
        extraction={extraction({
          authors: [author({ name: "   ", email: "jane@example.edu" })],
        })}
      />
    );
    expect(authorsCell().textContent).toBe("jane@example.edu");
  });

  it("renders multiple authors, each on its own line", () => {
    render(
      <SubmissionDetails
        extraction={extraction({
          authors: [
            author({ name: "Jane Roe", email: "jane@example.edu" }),
            author({ name: "John Doe", affiliation: "Other Institute" }),
          ],
        })}
      />
    );
    expect(screen.getByText("Jane Roe")).toBeInTheDocument();
    expect(screen.getByText("John Doe")).toBeInTheDocument();
    // Stacked, not comma-joined: one child element per person, in a column.
    // SCOPE LIMIT: jsdom does no layout, so the column is pinned by its class —
    // the same approach the repo uses for other layout-only declarations.
    expect(authorsCell().children).toHaveLength(2);
    expect(authorsCell()).toHaveClass("flex", "flex-col");
  });

  it("keeps a full mention intact among partial ones", () => {
    render(
      <SubmissionDetails
        extraction={extraction({
          authors: [
            author({ name: "Solo Name" }),
            author({
              name: "Jane Roe",
              email: "jane@example.edu",
              affiliation: "Example University",
            }),
            author({ email: "silent@example.edu" }),
          ],
        })}
      />
    );
    const lines = Array.from(authorsCell().children).map((el) => el.textContent);
    expect(lines).toEqual([
      "Solo Name",
      "Jane Roe·jane@example.edu·Example University",
      "silent@example.edu",
    ]);
  });

  it("preserves the order the backend supplied", () => {
    // The extractor dedupes and returns first-seen order (sender first on the
    // regex path); re-sorting here would discard that signal.
    render(
      <SubmissionDetails
        extraction={extraction({
          authors: [author({ name: "Zoe Last" }), author({ name: "Aaron First" })],
        })}
      />
    );
    const lines = Array.from(authorsCell().children).map((el) => el.textContent);
    expect(lines).toEqual(["Zoe Last", "Aaron First"]);
  });

  it("does NOT render the email as a mailto link", () => {
    // Replies must travel through Zendesk; a mailto invites answering
    // out-of-band, losing the ticket's audit trail and its send-path tagging.
    render(
      <SubmissionDetails
        extraction={extraction({ authors: [author({ email: "jane@example.edu" })] })}
      />
    );
    expect(screen.queryByRole("link")).toBeNull();
    expect(
      screen.getByText("jane@example.edu").closest("a")
    ).toBeNull();
  });

  it("hides the separator from assistive tech", () => {
    render(
      <SubmissionDetails
        extraction={extraction({
          authors: [author({ name: "Jane Roe", email: "jane@example.edu" })],
        })}
      />
    );
    const separators = Array.from(authorsCell().querySelectorAll("span")).filter(
      (el) => el.textContent === "·"
    );
    expect(separators).toHaveLength(1);
    expect(separators[0]).toHaveAttribute("aria-hidden");
  });

  it("skips a mention with no populated field without leaving a blank line", () => {
    render(
      <SubmissionDetails
        extraction={extraction({
          authors: [author({ name: "Jane Roe" }), author()],
        })}
      />
    );
    expect(authorsCell().textContent).toBe("Jane Roe");
    // Asserted on ELEMENTS, not text: an empty <span> contributes no text but
    // would still render as a blank line, which textContent alone cannot see.
    expect(authorsCell().children).toHaveLength(1);
  });
});

describe("SubmissionDetails — all three rows together", () => {
  it("renders number, forum id and authors in one grid, in order", () => {
    render(
      <SubmissionDetails
        extraction={extraction({
          submission_number: "22336",
          openreview_forum_id: "Ab3xY9kLm2",
          authors: [author({ name: "Jane Roe", email: "jane@example.edu" })],
        })}
      />
    );
    const grid = panel()!.querySelector(".grid")!;
    expect(grid).not.toBeNull();

    // All three labels live in the SAME grid, so the value column stays aligned.
    for (const label of ["Number", "OpenReview", "Authors"]) {
      expect(grid).toContainElement(screen.getByText(label));
    }

    const labels = Array.from(grid.children)
      .map((el) => el.textContent)
      .filter((text) => ["Number", "OpenReview", "Authors"].includes(text ?? ""));
    expect(labels).toEqual(["Number", "OpenReview", "Authors"]);
  });

  it("keeps 2b's grid assumption: one label and one value cell per row", () => {
    render(
      <SubmissionDetails
        extraction={extraction({
          submission_number: "22336",
          openreview_forum_id: "Ab3xY9kLm2",
          authors: [author({ name: "Jane Roe" })],
        })}
      />
    );
    // 3 rows x 2 columns — a stray wrapper would break the column alignment
    // the shared grid exists to provide.
    expect(panel()!.querySelector(".grid")!.children).toHaveLength(6);
  });

  it("still renders every other row when authors are absent", () => {
    render(
      <SubmissionDetails
        extraction={extraction({
          submission_number: "22336",
          openreview_forum_id: "Ab3xY9kLm2",
        })}
      />
    );
    expect(screen.queryByText("Authors")).toBeNull();
    expect(panel()!.querySelector(".grid")!.children).toHaveLength(4);
  });
});
