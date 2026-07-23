/**
 * CopyLinkButton (Piece C5) — copies the shareable ticket URL to the clipboard.
 *
 * Uses fireEvent (not userEvent) for the click: userEvent.setup() installs its
 * own navigator.clipboard stub, which would shadow the mock we assert on (and
 * re-provide a working clipboard in the "absent API" case). fireEvent triggers
 * the handler without touching the clipboard, so our mock is authoritative.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import { CopyLinkButton } from "./CopyLinkButton";

const writeText = vi.fn();
let originalClipboard: PropertyDescriptor | undefined;
let originalLocation: PropertyDescriptor | undefined;

beforeEach(() => {
  writeText.mockReset().mockResolvedValue(undefined);

  // Mock the clipboard API.
  originalClipboard = Object.getOwnPropertyDescriptor(navigator, "clipboard");
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
  });

  // Mock window.location.origin (deterministic, environment-independent).
  originalLocation = Object.getOwnPropertyDescriptor(window, "location");
  Object.defineProperty(window, "location", {
    value: { origin: "https://app.example.com" },
    configurable: true,
  });
});

afterEach(() => {
  if (originalClipboard) Object.defineProperty(navigator, "clipboard", originalClipboard);
  if (originalLocation) Object.defineProperty(window, "location", originalLocation);
});

describe("CopyLinkButton", () => {
  it("copies `${origin}/tickets/{ticketId}` on click", async () => {
    render(<CopyLinkButton ticketId={21567} />);

    fireEvent.click(
      screen.getByRole("button", { name: /copy shareable ticket link/i })
    );

    // Confirmation implies the async copy resolved.
    expect(await screen.findByText("Copied!")).toBeInTheDocument();
    expect(writeText).toHaveBeenCalledWith(
      "https://app.example.com/tickets/21567"
    );
  });

  it("shows a success confirmation after copy", async () => {
    render(<CopyLinkButton ticketId={21567} />);

    expect(screen.getByText("Copy link")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button"));

    expect(await screen.findByText("Copied!")).toBeInTheDocument();
  });

  it("renders nothing when the ticket id is missing (no crash)", () => {
    const { container } = render(<CopyLinkButton ticketId={null} />);

    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("handles a clipboard failure gracefully (no throw, shows a notice)", async () => {
    writeText.mockRejectedValue(new Error("clipboard blocked"));
    render(<CopyLinkButton ticketId={21567} />);

    // Must not throw out of the handler.
    fireEvent.click(screen.getByRole("button"));

    expect(await screen.findByText("Copy failed")).toBeInTheDocument();
    // Component is intact (didn't crash).
    expect(screen.getByRole("button")).toBeInTheDocument();
  });

  it("handles an entirely absent clipboard API without throwing", async () => {
    Object.defineProperty(navigator, "clipboard", {
      value: undefined,
      configurable: true,
    });
    render(<CopyLinkButton ticketId={21567} />);

    fireEvent.click(screen.getByRole("button"));

    expect(await screen.findByText("Copy failed")).toBeInTheDocument();
  });
});
