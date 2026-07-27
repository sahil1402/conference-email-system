import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { Pagination, getPageItems } from "./Pagination";

describe("getPageItems — truncation", () => {
  it("shows every page when there are few (<= 7)", () => {
    expect(getPageItems(3, 5)).toEqual([1, 2, 3, 4, 5]);
    expect(getPageItems(1, 7)).toEqual([1, 2, 3, 4, 5, 6, 7]);
  });

  it("truncates with a single trailing ellipsis near the start", () => {
    // page 1 of 18 → 1 2 … 18
    expect(getPageItems(1, 18)).toEqual([1, 2, "ellipsis", 18]);
  });

  it("collapses a one-page gap into the number (no lone ellipsis)", () => {
    // page 4 of 18 → 1 2 3 4 5 … 18  (the gap 1→3 becomes "2", not "…")
    expect(getPageItems(4, 18)).toEqual([1, 2, 3, 4, 5, "ellipsis", 18]);
  });

  it("truncates on both sides in the middle", () => {
    // page 10 of 18 → 1 … 9 10 11 … 18
    expect(getPageItems(10, 18)).toEqual([
      1,
      "ellipsis",
      9,
      10,
      11,
      "ellipsis",
      18,
    ]);
  });

  it("truncates near the end", () => {
    // page 18 of 18 → 1 … 17 18
    expect(getPageItems(18, 18)).toEqual([1, "ellipsis", 17, 18]);
  });

  it("handles degenerate counts", () => {
    expect(getPageItems(1, 1)).toEqual([1]);
    expect(getPageItems(1, 0)).toEqual([]);
  });
});

describe("Pagination — rendering", () => {
  it("renders a button per page plus prev/next chevrons", () => {
    render(<Pagination page={1} pageCount={5} onPageChange={vi.fn()} />);

    for (let p = 1; p <= 5; p++) {
      expect(screen.getByRole("button", { name: `Page ${p}` })).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: "Previous page" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next page" })).toBeInTheDocument();
  });

  it("renders nothing for a single page", () => {
    const { container } = render(
      <Pagination page={1} pageCount={1} onPageChange={vi.fn()} />
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("marks the current page with aria-current, and no other", () => {
    render(<Pagination page={3} pageCount={5} onPageChange={vi.fn()} />);

    expect(screen.getByRole("button", { name: "Page 3" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    expect(screen.getByRole("button", { name: "Page 2" })).not.toHaveAttribute(
      "aria-current"
    );
  });

  it("shows an ellipsis when the page count is large", () => {
    render(<Pagination page={10} pageCount={18} onPageChange={vi.fn()} />);
    expect(screen.getAllByText("…").length).toBeGreaterThan(0);
    // Truncated → NOT every page is a button.
    expect(screen.queryByRole("button", { name: "Page 5" })).toBeNull();
    expect(screen.getByRole("button", { name: "Page 1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Page 18" })).toBeInTheDocument();
  });

  it("disables prev on the first page and next on the last", () => {
    const { rerender } = render(
      <Pagination page={1} pageCount={5} onPageChange={vi.fn()} />
    );
    expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next page" })).not.toBeDisabled();

    rerender(<Pagination page={5} pageCount={5} onPageChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Previous page" })).not.toBeDisabled();
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
  });
});

describe("Pagination — interaction", () => {
  it("calls onPageChange with the clicked page number", () => {
    const onPageChange = vi.fn();
    render(<Pagination page={1} pageCount={5} onPageChange={onPageChange} />);

    fireEvent.click(screen.getByRole("button", { name: "Page 3" }));
    expect(onPageChange).toHaveBeenCalledWith(3);
  });

  it("prev/next step by one from the current page", () => {
    const onPageChange = vi.fn();
    render(<Pagination page={3} pageCount={5} onPageChange={onPageChange} />);

    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(onPageChange).toHaveBeenLastCalledWith(4);

    fireEvent.click(screen.getByRole("button", { name: "Previous page" }));
    expect(onPageChange).toHaveBeenLastCalledWith(2);
  });
});
