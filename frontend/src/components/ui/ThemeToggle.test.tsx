import { describe, expect, it, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ThemeToggle } from "./ThemeToggle";

/** useTheme reads the attribute the anti-flash script sets; reset it per test. */
beforeEach(() => {
  document.documentElement.removeAttribute("data-theme");
  window.localStorage.clear();
});

function setLightTheme() {
  document.documentElement.setAttribute("data-theme", "light");
}

describe("ThemeToggle — compact variant", () => {
  it("shows the Moon icon while the theme is dark", async () => {
    render(<ThemeToggle compact />);

    const button = await screen.findByRole("switch", {
      name: "Switch to light mode",
    });
    expect(button).toHaveAttribute("aria-checked", "false");
    expect(button.querySelector("svg.lucide-moon")).not.toBeNull();
    expect(button.querySelector("svg.lucide-sun")).toBeNull();
  });

  it("shows the Sun icon while the theme is light", async () => {
    setLightTheme();
    render(<ThemeToggle compact />);

    const button = await screen.findByRole("switch", {
      name: "Switch to dark mode",
    });
    await waitFor(() =>
      expect(button).toHaveAttribute("aria-checked", "true")
    );
    expect(button.querySelector("svg.lucide-sun")).not.toBeNull();
    expect(button.querySelector("svg.lucide-moon")).toBeNull();
  });

  it("toggles the theme on click, persisting the choice", async () => {
    const user = userEvent.setup();
    render(<ThemeToggle compact />);

    await user.click(screen.getByRole("switch"));

    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(window.localStorage.getItem("confmail-theme")).toBe("light");

    await user.click(screen.getByRole("switch"));

    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(window.localStorage.getItem("confmail-theme")).toBe("dark");
  });

  it("describes the action in a tooltip, per theme state", async () => {
    const user = userEvent.setup();
    render(<ThemeToggle compact />);

    await user.hover(screen.getByRole("switch"));
    await waitFor(() =>
      expect(screen.getByRole("tooltip")).toHaveTextContent(
        "Switch to light mode"
      )
    );
  });

  it("is sized to match the nav rail's 36x36 icon targets", () => {
    render(<ThemeToggle compact />);
    expect(screen.getByRole("switch").className).toContain("h-9 w-9");
  });
});

describe("ThemeToggle — default pill variant (unchanged)", () => {
  it("still renders the 52px pill with its generic label", () => {
    render(<ThemeToggle />);

    const button = screen.getByRole("switch", {
      name: "Toggle light/dark theme",
    });
    expect(button.className).toContain("w-[52px]");
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("still toggles the theme on click", async () => {
    const user = userEvent.setup();
    render(<ThemeToggle />);

    await user.click(screen.getByRole("switch"));

    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(window.localStorage.getItem("confmail-theme")).toBe("light");
  });
});
