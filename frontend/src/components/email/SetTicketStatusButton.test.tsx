import { describe, it, expect, beforeAll, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SetTicketStatusButton } from "./SetTicketStatusButton";

beforeAll(() => {
  // Radix DropdownMenu needs these in jsdom.
  window.HTMLElement.prototype.hasPointerCapture = vi.fn();
  window.HTMLElement.prototype.releasePointerCapture = vi.fn();
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

describe("SetTicketStatusButton", () => {
  it("primary click marks solved", async () => {
    const onSetStatus = vi.fn();
    const user = userEvent.setup();
    render(<SetTicketStatusButton onSetStatus={onSetStatus} />);

    await user.click(screen.getByRole("button", { name: /mark solved · no reply/i }));
    expect(onSetStatus).toHaveBeenCalledWith("solved");
  });

  it("dropdown offers new / open / solved", async () => {
    const onSetStatus = vi.fn();
    const user = userEvent.setup();
    render(<SetTicketStatusButton onSetStatus={onSetStatus} />);

    await user.click(
      screen.getByRole("button", { name: /set another status without replying/i })
    );
    await user.click(await screen.findByRole("menuitem", { name: /mark as new/i }));
    expect(onSetStatus).toHaveBeenCalledWith("new");
  });

  it("disabled prevents the primary action", async () => {
    const onSetStatus = vi.fn();
    const user = userEvent.setup();
    render(<SetTicketStatusButton onSetStatus={onSetStatus} disabled />);

    const primary = screen.getByRole("button", { name: /mark solved · no reply/i });
    expect(primary).toBeDisabled();
    await user.click(primary);
    expect(onSetStatus).not.toHaveBeenCalled();
  });

  it("loading disables both controls", () => {
    render(<SetTicketStatusButton onSetStatus={vi.fn()} loading />);
    expect(screen.getByRole("button", { name: /mark solved · no reply/i })).toBeDisabled();
    expect(
      screen.getByRole("button", { name: /set another status without replying/i })
    ).toBeDisabled();
  });
});
