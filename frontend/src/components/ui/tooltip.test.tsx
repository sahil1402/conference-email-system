import { describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "./tooltip";

// The ResizeObserver polyfill Radix's popper needs now lives in
// vitest.setup.ts, shared with the other popper-based suites.

function Subject() {
  return (
    // delayDuration 0 so the tooltip opens without waiting out the timer.
    <TooltipProvider delayDuration={0}>
      <Tooltip>
        <TooltipTrigger aria-label="Email Queue">icon</TooltipTrigger>
        <TooltipContent>Email Queue</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

describe("Tooltip", () => {
  it("renders the trigger, with no tooltip content until interaction", () => {
    render(<Subject />);
    expect(
      screen.getByRole("button", { name: "Email Queue" })
    ).toBeInTheDocument();
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("shows the content on hover", async () => {
    const user = userEvent.setup();
    render(<Subject />);

    await user.hover(screen.getByRole("button", { name: "Email Queue" }));

    await waitFor(() =>
      expect(screen.getByRole("tooltip")).toHaveTextContent("Email Queue")
    );
  });

  it("shows the content on keyboard focus", async () => {
    const user = userEvent.setup();
    render(<Subject />);

    await user.tab();

    expect(screen.getByRole("button", { name: "Email Queue" })).toHaveFocus();
    await waitFor(() =>
      expect(screen.getByRole("tooltip")).toHaveTextContent("Email Queue")
    );
  });
});
