import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { AppShell } from "./AppShell";

// AppShell renders Sidebar, which reads the active route.
vi.mock("next/navigation", () => ({
  usePathname: () => "/dashboard",
}));

describe("AppShell", () => {
  it("renders the desktop top bar (brand subtitle is unique to it)", () => {
    render(
      <AppShell>
        <div>child</div>
      </AppShell>
    );

    // "ConfMail" also appears in the mobile bar; the subtitle is desktop-only,
    // so it uniquely proves the desktop bar is mounted.
    expect(screen.getByText("Conference Email System")).toBeInTheDocument();
  });

  it("still renders the mobile top bar and the children", () => {
    render(
      <AppShell>
        <div>child-content</div>
      </AppShell>
    );

    // Two "ConfMail" lockups: mobile header + desktop bar. Mutually exclusive
    // at runtime via md: gating, but both are in the DOM for jsdom.
    expect(screen.getAllByText("ConfMail")).toHaveLength(2);
    expect(screen.getByText("child-content")).toBeInTheDocument();
  });

  it("offsets <main> below the top bar at both breakpoints", () => {
    const { container } = render(
      <AppShell>
        <div>child</div>
      </AppShell>
    );
    const main = container.querySelector("main")!;

    // Mobile bar offset, desktop bar offset, rail offset — and no leftover
    // md:pt-0 from before the desktop bar existed.
    expect(main.className).toContain("pt-14");
    expect(main.className).toContain("md:pt-[var(--topbar-height)]");
    expect(main.className).toContain("md:ml-[var(--rail-width)]");
    expect(main.className).not.toContain("md:pt-0");
  });
});
