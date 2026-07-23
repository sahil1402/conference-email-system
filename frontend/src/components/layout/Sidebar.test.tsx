import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import { Sidebar } from "./Sidebar";

// usePathname drives active-item detection; swap it per test.
let mockPathname = "/dashboard";
vi.mock("next/navigation", () => ({
  usePathname: () => mockPathname,
}));

/** Labels of NAV_ITEMS, in render order. */
const NAV_LABELS = [
  "Dashboard",
  "Email Queue",
  "Knowledge Base",
  "Analytics",
  "Auto-Replies",
  "Audit Log",
];

beforeEach(() => {
  mockPathname = "/dashboard";
});

describe("Sidebar (icon-only rail)", () => {
  it("renders one link per nav item, each named by its aria-label", () => {
    render(<Sidebar />);
    const nav = screen.getByRole("navigation");

    for (const label of NAV_LABELS) {
      expect(within(nav).getByRole("link", { name: label })).toBeInTheDocument();
    }
    expect(within(nav).getAllByRole("link")).toHaveLength(NAV_LABELS.length);
  });

  it("shows no visible text in the nav — icons only", () => {
    render(<Sidebar />);
    const nav = screen.getByRole("navigation");

    // The accessible name comes from aria-label, not rendered text content.
    for (const link of within(nav).getAllByRole("link")) {
      expect(link).toHaveTextContent("");
    }
    expect(nav).toHaveTextContent("");
  });

  it("points each link at its route", () => {
    render(<Sidebar />);
    const nav = screen.getByRole("navigation");

    expect(within(nav).getByRole("link", { name: "Dashboard" })).toHaveAttribute(
      "href",
      "/dashboard"
    );
    expect(
      within(nav).getByRole("link", { name: "Email Queue" })
    ).toHaveAttribute("href", "/queue");
  });

  it("marks the current route active and leaves the others inactive", () => {
    mockPathname = "/queue";
    render(<Sidebar />);
    const nav = screen.getByRole("navigation");

    const active = within(nav).getByRole("link", { name: "Email Queue" });
    const inactive = within(nav).getByRole("link", { name: "Dashboard" });

    expect(active.style.color).toBe("var(--accent)");
    expect(active.style.borderLeftColor).toBe("var(--accent)");
    expect(inactive.style.color).toBe("var(--text-secondary)");
    expect(inactive.style.borderLeftColor).toBe("transparent");
  });

  it("treats a nested route as active for its section", () => {
    mockPathname = "/knowledge-base/policy_123";
    render(<Sidebar />);
    const nav = screen.getByRole("navigation");

    expect(
      within(nav).getByRole("link", { name: "Knowledge Base" }).style.color
    ).toBe("var(--accent)");
  });

  it("gives inactive items a CSS hover class, not an inline-style handler", () => {
    mockPathname = "/queue";
    render(<Sidebar />);
    const inactive = within(screen.getByRole("navigation")).getByRole("link", {
      name: "Dashboard",
    });

    expect(inactive.className).toContain("hover:bg-[var(--surface-raised)]");
  });

  it("does not mutate inline styles on mouse enter/leave (handlers removed)", () => {
    mockPathname = "/queue";
    render(<Sidebar />);
    const inactive = within(screen.getByRole("navigation")).getByRole("link", {
      name: "Dashboard",
    });

    // The old imperative handlers wrote backgroundColor onto the element; with
    // CSS hover there is nothing inline to change.
    expect(inactive.style.backgroundColor).toBe("");
    fireEvent.mouseEnter(inactive);
    expect(inactive.style.backgroundColor).toBe("");
    fireEvent.mouseLeave(inactive);
    expect(inactive.style.backgroundColor).toBe("");
  });

  it("keeps the active treatment intact on hover, with no hover class applied", () => {
    mockPathname = "/queue";
    render(<Sidebar />);
    const active = within(screen.getByRole("navigation")).getByRole("link", {
      name: "Email Queue",
    });

    // Active items opt out of the hover background entirely…
    expect(active.className).not.toContain("hover:bg-");

    // …and hovering must not disturb the active background/border/color.
    fireEvent.mouseEnter(active);
    expect(active.style.backgroundColor).toBe("var(--accent-subtle)");
    expect(active.style.borderLeftColor).toBe("var(--accent)");
    expect(active.style.color).toBe("var(--accent)");
    fireEvent.mouseLeave(active);
    expect(active.style.backgroundColor).toBe("var(--accent-subtle)");
  });

  it("calls onNavigate when a nav link is clicked (mobile drawer close)", () => {
    const onNavigate = vi.fn();
    render(<Sidebar onNavigate={onNavigate} />);

    // Swallow the anchor's default navigation — jsdom can't navigate and would
    // log "Not implemented: navigation". The React handler still runs first.
    const swallow = (e: MouseEvent) => e.preventDefault();
    document.addEventListener("click", swallow);
    screen.getByRole("link", { name: "Analytics" }).click();
    document.removeEventListener("click", swallow);

    expect(onNavigate).toHaveBeenCalledTimes(1);
  });
});
