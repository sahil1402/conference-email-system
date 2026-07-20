import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { SourceToggle } from "./SourceToggle";

describe("SourceToggle (self-hiding)", () => {
  it("renders nothing when only one source is present", () => {
    const { container } = render(
      <SourceToggle sources={["toy_dataset"]} value="all" onChange={vi.fn()} />
    );
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByRole("group", { name: /source/i })).toBeNull();
  });

  it("renders nothing when there are zero sources", () => {
    const { container } = render(
      <SourceToggle sources={[]} value="all" onChange={vi.fn()} />
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows All + one button per source when both are present", () => {
    render(
      <SourceToggle
        sources={["toy_dataset", "zendesk"]}
        value="all"
        onChange={vi.fn()}
      />
    );
    expect(screen.getByRole("group", { name: /source/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "All" })).toBeInTheDocument();
    // Friendly labels, ordered zendesk-first per SOURCE_ORDER.
    expect(screen.getByRole("button", { name: "Zendesk" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Toy Dataset" })).toBeInTheDocument();
  });

  it("calls onChange with the source value when a segment is clicked", () => {
    const onChange = vi.fn();
    render(
      <SourceToggle
        sources={["toy_dataset", "zendesk"]}
        value="all"
        onChange={onChange}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: "Zendesk" }));
    expect(onChange).toHaveBeenCalledWith("zendesk");
    fireEvent.click(screen.getByRole("button", { name: "All" }));
    expect(onChange).toHaveBeenCalledWith("all");
  });

  it("marks the active segment via aria-pressed", () => {
    render(
      <SourceToggle
        sources={["toy_dataset", "zendesk"]}
        value="zendesk"
        onChange={vi.fn()}
      />
    );
    expect(screen.getByRole("button", { name: "Zendesk" })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute(
      "aria-pressed",
      "false"
    );
  });
});
