/**
 * SubmissionDetails (2a) — the panel's render / don't-render contract, plus
 * submission_number output.
 *
 * SCOPE LIMIT: only submission_number has UI in this subtask. The emptiness
 * check already considers openreview_forum_id and authors, so the "renders on
 * those alone" cases below assert the CONTAINER appears — deliberately not the
 * field output, which is 2b's and 2c's job. Those tests are what stop a later
 * subtask from having to revisit this component's render decision.
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

describe("SubmissionDetails — other fields keep the panel alive (2b/2c)", () => {
  it("still renders when only openreview_forum_id is present", () => {
    render(
      <SubmissionDetails
        extraction={extraction({ openreview_forum_id: "Ab3xY9kLm2" })}
      />
    );
    // The container must appear even though 2b has not wired the field's output.
    expect(panel()).toBeInTheDocument();
    expect(screen.getByText("Submission Details")).toBeInTheDocument();
    // ...and the number row must be absent, since there is no number.
    expect(screen.queryByText("Number")).toBeNull();
  });

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
