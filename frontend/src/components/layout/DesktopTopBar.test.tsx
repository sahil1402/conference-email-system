import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { DesktopTopBar } from "./DesktopTopBar";

describe("DesktopTopBar", () => {
  it("renders the brand title and subtitle", () => {
    render(<DesktopTopBar />);

    expect(screen.getByText("ConfMail")).toBeInTheDocument();
    expect(screen.getByText("Conference Email System")).toBeInTheDocument();
  });

  it("is desktop-only — hidden below md, flex at md+", () => {
    // jsdom can't evaluate media queries, so assert the gating classes are
    // present (same approach as the queue's responsive tests). This is the
    // inverse of AppShell's mobile bar (md:hidden), so only one ever shows.
    const { container } = render(<DesktopTopBar />);
    const bar = container.querySelector("header")!;

    expect(bar.className).toContain("hidden");
    expect(bar.className).toContain("md:flex");
  });

  it("starts after the rail and spans the token height", () => {
    const { container } = render(<DesktopTopBar />);
    const bar = container.querySelector("header")!;

    expect(bar.className).toContain("left-[var(--rail-width)]");
    expect(bar.className).toContain("right-0");
    expect(bar.className).toContain("h-[var(--topbar-height)]");
  });

  it("merges a caller className (BR4 adds z-index here) without dropping its own", () => {
    const { container } = render(<DesktopTopBar className="z-30" />);
    const bar = container.querySelector("header")!;

    expect(bar.className).toContain("z-30");
    expect(bar.className).toContain("left-[var(--rail-width)]"); // own classes kept
  });

  it("hides the decorative separator from assistive tech", () => {
    render(<DesktopTopBar />);
    // The middot between title and subtitle is aria-hidden, so the accessible
    // reading is "ConfMail Conference Email System", not "ConfMail · …".
    const sep = screen.getByText("·");
    expect(sep).toHaveAttribute("aria-hidden");
  });
});
