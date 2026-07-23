import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { HighlightText } from "./HighlightText";

describe("HighlightText", () => {
  it("wraps a matched snippet in <mark>", () => {
    render(
      <HighlightText
        text="Reviews are due within 14 days of assignment."
        snippets={["due within 14 days"]}
      />
    );
    const mark = screen.getByText("due within 14 days");
    expect(mark.tagName).toBe("MARK");
  });

  it("matches case-insensitively but preserves the original casing", () => {
    render(<HighlightText text="Due Within 14 Days" snippets={["due within 14 days"]} />);
    expect(screen.getByText("Due Within 14 Days").tagName).toBe("MARK");
  });

  it("renders the full text intact around the match", () => {
    const { container } = render(
      <HighlightText text="a due within 14 days b" snippets={["due within 14 days"]} />
    );
    expect(container.textContent).toBe("a due within 14 days b");
    expect(container.querySelector("mark")?.textContent).toBe("due within 14 days");
  });

  it("renders plain text with no <mark> when there are no snippets", () => {
    const { container } = render(<HighlightText text="hello world" snippets={[]} />);
    expect(container.querySelector("mark")).toBeNull();
    expect(container.textContent).toBe("hello world");
  });

  it("does not highlight a snippet that is not present verbatim", () => {
    const { container } = render(
      <HighlightText text="due within 14 days" snippets={["30 days"]} />
    );
    expect(container.querySelector("mark")).toBeNull();
  });
});
