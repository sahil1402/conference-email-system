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

  it("dropdown offers new / open / pending / solved, in Zendesk's native order", async () => {
    // Asserts the FULL option set, not just one item: the previous version of
    // this test clicked only "Mark as new", so "pending" could have been absent
    // (or misnamed) and it would still have passed.
    const onSetStatus = vi.fn();
    const user = userEvent.setup();
    render(<SetTicketStatusButton onSetStatus={onSetStatus} />);

    await user.click(
      screen.getByRole("button", { name: /set another status without replying/i })
    );

    // Order is documented intent (STATUS_OPTIONS: "in Zendesk's native order"),
    // so it is pinned rather than treated as incidental.
    const items = await screen.findAllByRole("menuitem");
    expect(items.map((i) => i.textContent)).toEqual([
      "Mark as new",
      "Mark as open",
      "Mark as pending",
      "Mark as solved",
    ]);
  });

  // Radix closes the menu on select, so each status needs its own open→click
  // cycle — hence a per-case test rather than four clicks in one body.
  it.each([
    ["new", /mark as new/i],
    ["open", /mark as open/i],
    ["pending", /mark as pending/i],
    ["solved", /mark as solved/i],
  ])("dropdown item for %s fires onSetStatus with that status", async (status, name) => {
    const onSetStatus = vi.fn();
    const user = userEvent.setup();
    render(<SetTicketStatusButton onSetStatus={onSetStatus} />);

    await user.click(
      screen.getByRole("button", { name: /set another status without replying/i })
    );
    await user.click(await screen.findByRole("menuitem", { name }));

    expect(onSetStatus).toHaveBeenCalledWith(status);
    expect(onSetStatus).toHaveBeenCalledTimes(1);
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
