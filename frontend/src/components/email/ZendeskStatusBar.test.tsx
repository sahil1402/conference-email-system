import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import { ZendeskStatusBar } from "./ZendeskStatusBar";

const COUNTS = { new: 3, open: 2, solved: 1 };

describe("ZendeskStatusBar", () => {
  it("renders a row per status with the correct count", () => {
    render(
      <ZendeskStatusBar counts={COUNTS} selected={null} onSelect={vi.fn()} />
    );
    const newRow = screen.getByRole("button", { name: /new/i });
    expect(within(newRow).getByText("3")).toBeInTheDocument();
    expect(
      within(screen.getByRole("button", { name: /open/i })).getByText("2")
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("button", { name: /solved/i })).getByText("1")
    ).toBeInTheDocument();
  });

  it("orders statuses canonically (new → open → solved)", () => {
    render(
      <ZendeskStatusBar counts={COUNTS} selected={null} onSelect={vi.fn()} />
    );
    const group = screen.getByRole("group", { name: /zendesk status/i });
    const labels = within(group)
      .getAllByRole("button")
      .map((b) => b.textContent);
    // Each button text is "<Label><count>"; assert the label order.
    expect(labels[0]).toMatch(/^New/);
    expect(labels[1]).toMatch(/^Open/);
    expect(labels[2]).toMatch(/^Solved/);
  });

  it("renders nothing when there are no counts", () => {
    const { container } = render(
      <ZendeskStatusBar counts={{}} selected={null} onSelect={vi.fn()} />
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("selects a status on click", () => {
    const onSelect = vi.fn();
    render(
      <ZendeskStatusBar counts={COUNTS} selected={null} onSelect={onSelect} />
    );
    fireEvent.click(screen.getByRole("button", { name: /open/i }));
    expect(onSelect).toHaveBeenCalledWith("open");
  });

  it("clears the filter when the active status is clicked again", () => {
    const onSelect = vi.fn();
    render(
      <ZendeskStatusBar counts={COUNTS} selected="open" onSelect={onSelect} />
    );
    fireEvent.click(screen.getByRole("button", { name: /open/i }));
    expect(onSelect).toHaveBeenCalledWith(null);
  });

  it("marks the selected status via aria-pressed and offers a Clear control", () => {
    const onSelect = vi.fn();
    render(
      <ZendeskStatusBar counts={COUNTS} selected="new" onSelect={onSelect} />
    );
    expect(screen.getByRole("button", { name: /new/i })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
    const clear = screen.getByRole("button", { name: /clear/i });
    fireEvent.click(clear);
    expect(onSelect).toHaveBeenCalledWith(null);
  });
});
