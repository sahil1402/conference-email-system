/**
 * Filter-column extraction (N3a–N3g) — integration test over the REAL QueuePage.
 *
 * The filters used to render through a React portal into a slot inside the
 * Sidebar; they are now page-owned, rendering directly in their own column. This
 * suite covers what that move put at risk: that the panel really is inside the
 * column (not portalled elsewhere), and that every control still drives the
 * page's filter state.
 *
 * Component-level behaviour of SourceToggle / ZendeskStatusBar is already
 * covered by their own unit tests and is not repeated here.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import QueuePage from "./page";

const state = vi.hoisted(() => ({
  chairs: [
    { id: 1, name: "Program Chair", role_title: "PC", areas: [], active: true },
    { id: 2, name: "Local Arrangements", role_title: "LA", areas: [], active: true },
  ],
}));

vi.mock("@/hooks/useEmailQueue", () => ({
  useEmailQueue: () => ({
    emails: [],
    total: 0,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
}));
vi.mock("@/hooks/useQueueFacets", () => ({
  useQueueFacets: () => ({
    byZendeskStatus: {},
    bySource: {},
    sources: [],
    isLoading: false,
    isError: false,
  }),
}));
vi.mock("@/hooks/useChairs", () => ({
  useChairs: () => ({
    chairs: state.chairs,
    byId: new Map(state.chairs.map((c) => [c.id, c])),
    isLoading: false,
    isError: false,
  }),
}));
vi.mock("@/hooks/useAppConfig", () => ({
  useAppConfig: () => ({ allowAutoSend: false }),
}));
vi.mock("@/hooks/useEmailQueueStream", () => ({
  useEmailQueueStream: () => ({ status: "live" }),
}));
vi.mock("@/hooks/useEmailThread", () => ({
  useEmailThread: () => ({ messages: [], isLoading: false, isError: false }),
}));

function renderQueue() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <QueuePage />
    </QueryClientProvider>
  );
}

const searchBox = () => screen.getByPlaceholderText(/search subject or sender/i);

/**
 * The filter column. Queried by [data-collapsed] rather than a width class or a
 * descendant, since both change with the collapse state.
 */
const column = () =>
  document.querySelector<HTMLElement>("[data-collapsed]")!;

beforeEach(() => {
  window.localStorage.clear();
});

describe("queue layout — full-height without top-bar overflow (BR4)", () => {
  it("sizes the split-pane root to viewport minus the top bar, not a bare 100vh", () => {
    renderQueue();
    // Root is the filter column's parent flex row.
    const root = column().parentElement!;

    // Bare h-screen (100vh) under <main>'s top padding would overflow; the
    // container subtracts the bar height at each breakpoint instead.
    expect(root.className).toContain("h-[calc(100vh-3.5rem)]"); // mobile (pt-14)
    expect(root.className).toContain(
      "md:h-[calc(100vh-var(--topbar-height))]"
    ); // desktop
    expect(root.className).not.toContain("h-screen");
    expect(root.className).toContain("overflow-hidden");
  });
});

describe("filter column — placement", () => {
  it("renders the filter panel inside the page's own 256px column", () => {
    renderQueue();

    // The w-64 column added in N3a; the panel must live inside it.
    const column = searchBox().closest<HTMLElement>("div.w-64");
    expect(column).not.toBeNull();

    // Every filter control is in that same column.
    expect(within(column!).getByRole("button", { name: "FAQ" })).toBeInTheDocument();
    expect(
      within(column!).getByLabelText("Filter by assigned chair")
    ).toBeInTheDocument();
  });

  it("renders in the page tree, not through a portal to document.body", () => {
    const { container } = renderQueue();

    // Portalled content would be outside the render container; page-owned
    // content is inside it.
    expect(container.contains(searchBox())).toBe(true);
  });

  it("has no stray top border on the panel (the column's own border separates it)", () => {
    renderQueue();
    const panel = searchBox().closest<HTMLElement>("div.space-y-4");

    expect(panel).not.toBeNull();
    expect(panel!.style.borderTop).toBe("");
  });
});

describe("filter column — persisted collapse state (N4a)", () => {
  const KEY = "confmail.filterColumnCollapsed";

  it("defaults to expanded and persists that default", () => {
    renderQueue();

    expect(column()).toHaveAttribute("data-collapsed", "false");
    expect(window.localStorage.getItem(KEY)).toBe("false");
  });

  it("restores a persisted collapsed state on mount", () => {
    window.localStorage.setItem(KEY, JSON.stringify(true));

    renderQueue();

    expect(column()).toHaveAttribute("data-collapsed", "true");
  });

  it("keeps the stored value across a remount rather than resetting it", () => {
    window.localStorage.setItem(KEY, JSON.stringify(true));

    const first = renderQueue();
    expect(column()).toHaveAttribute("data-collapsed", "true");
    first.unmount();

    renderQueue();
    expect(column()).toHaveAttribute("data-collapsed", "true");
    expect(window.localStorage.getItem(KEY)).toBe("true");
  });
});

describe("filter column — collapse toggle button (N4b)", () => {

  it("renders the toggle, labelled for the action when expanded", () => {
    renderQueue();

    const button = screen.getByRole("button", { name: "Hide filters" });
    expect(button).toBeInTheDocument();
    expect(button).toHaveAttribute("aria-expanded", "true");
  });

  it("flips the collapse state on click", async () => {
    const user = userEvent.setup();
    renderQueue();
    expect(column()).toHaveAttribute("data-collapsed", "false");

    await user.click(screen.getByRole("button", { name: "Hide filters" }));

    expect(column()).toHaveAttribute("data-collapsed", "true");
    expect(window.localStorage.getItem("confmail.filterColumnCollapsed")).toBe(
      "true"
    );
  });

  it("relabels itself once collapsed, and toggles back", async () => {
    const user = userEvent.setup();
    renderQueue();

    await user.click(screen.getByRole("button", { name: "Hide filters" }));

    const button = screen.getByRole("button", { name: "Show filters" });
    expect(button).toHaveAttribute("aria-expanded", "false");

    await user.click(button);

    expect(column()).toHaveAttribute("data-collapsed", "false");
    expect(screen.getByRole("button", { name: "Hide filters" })).toBeInTheDocument();
  });

  it("shows the action label in a tooltip on hover", async () => {
    const user = userEvent.setup();
    renderQueue();

    await user.hover(screen.getByRole("button", { name: "Hide filters" }));

    await waitFor(() =>
      expect(screen.getByRole("tooltip")).toHaveTextContent("Hide filters")
    );
  });

});

describe("filter column — toggle stays flush-left in both states (BR3b)", () => {
  /** The toggle's wrapper div (its direct DOM parent — Tooltip parts add no DOM). */
  const toggleWrapper = (name: RegExp) =>
    screen.getByRole("button", { name }).parentElement!;

  it("uses a constant left inset when expanded", () => {
    renderQueue();
    const wrapper = toggleWrapper(/Hide filters/);

    expect(wrapper.className).toContain("px-2");
    // px-3 was the old expanded value that made the button jump between states.
    expect(wrapper.className).not.toContain("px-3");
  });

  it("keeps the same inset when collapsed — button doesn't jump", async () => {
    const user = userEvent.setup();
    renderQueue();
    const expandedCls = toggleWrapper(/Hide filters/).className;

    await user.click(screen.getByRole("button", { name: "Hide filters" }));

    const collapsedCls = toggleWrapper(/Show filters/).className;
    expect(collapsedCls).toContain("px-2");
    // Horizontal inset identical across states → no positional jump.
    expect(collapsedCls).toBe(expandedCls);
  });
});

describe("filter column — collapsed rendering (N4c)", () => {
  it("hides the filter panel when collapsed", async () => {
    const user = userEvent.setup();
    renderQueue();
    expect(searchBox()).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Hide filters" }));

    expect(
      screen.queryByPlaceholderText(/search subject or sender/i)
    ).toBeNull();
    expect(screen.queryByLabelText("Filter by assigned chair")).toBeNull();
    expect(screen.queryByRole("button", { name: "FAQ" })).toBeNull();
  });

  it("keeps the toggle reachable when collapsed, so it can re-expand", async () => {
    const user = userEvent.setup();
    renderQueue();

    await user.click(screen.getByRole("button", { name: "Hide filters" }));
    const reopen = screen.getByRole("button", { name: "Show filters" });
    expect(reopen).toBeInTheDocument();

    await user.click(reopen);

    expect(searchBox()).toBeInTheDocument();
  });

  it("shrinks to a 52px sliver when collapsed and back to 256px expanded", async () => {
    const user = userEvent.setup();
    renderQueue();
    expect(column().className).toContain("w-64");
    expect(column().className).not.toContain("w-[52px]");

    await user.click(screen.getByRole("button", { name: "Hide filters" }));

    expect(column().className).toContain("w-[52px]");
    expect(column().className).not.toContain("w-64");

    await user.click(screen.getByRole("button", { name: "Show filters" }));

    expect(column().className).toContain("w-64");
  });

  it("animates the width change rather than jumping", () => {
    renderQueue();

    // Named-property transition, matching the codebase's convention.
    expect(column().className).toContain("transition-[width]");
    expect(column().className).toContain("duration-200");
    // Stops the panel flashing a horizontal scrollbar mid-animation.
    expect(column().className).toContain("overflow-x-hidden");
  });

  it("renders the full panel alongside the toggle when expanded", () => {
    renderQueue();

    expect(screen.getByRole("button", { name: "Hide filters" })).toBeInTheDocument();
    expect(searchBox()).toBeInTheDocument();
    expect(screen.getByLabelText("Filter by assigned chair")).toBeInTheDocument();
  });

  it("restores the collapsed sliver on mount from persisted state", () => {
    window.localStorage.setItem("confmail.filterColumnCollapsed", "true");

    renderQueue();

    expect(column().className).toContain("w-[52px]");
    expect(
      screen.queryByPlaceholderText(/search subject or sender/i)
    ).toBeNull();
    expect(screen.getByRole("button", { name: "Show filters" })).toBeInTheDocument();
  });
});

/**
 * Collapsing frees chrome, so the list/detail panes get more room. jsdom's
 * viewport is 1024px, which is exactly where the clamp bites:
 *   expanded  → reserved 315, max = 1024 - 315 - 440 = 269
 *   collapsed → reserved 111, max = 1024 - 111 - 440 = 473
 */
describe("filter column — collapse frees space for list/detail (N4d)", () => {
  const LIST_KEY = "confmail.queueListWidth";
  const COLLAPSE_KEY = "confmail.filterColumnCollapsed";
  /** The resizable list column, whose width is an inline style. */
  const listPane = () => document.querySelector<HTMLElement>("aside")!;

  it("clamps the list harder while the filter column is expanded", () => {
    window.localStorage.setItem(LIST_KEY, JSON.stringify(640));

    renderQueue();

    expect(listPane().style.width).toBe("269px");
  });

  it("allows a wider list when mounted with the filter column collapsed", () => {
    window.localStorage.setItem(LIST_KEY, JSON.stringify(640));
    window.localStorage.setItem(COLLAPSE_KEY, JSON.stringify(true));

    renderQueue();

    // 204px of chrome freed → the ceiling rises from 269 to 473.
    expect(listPane().style.width).toBe("473px");
  });

  it("re-clamps at runtime when expanding consumes the space back", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem(LIST_KEY, JSON.stringify(640));
    window.localStorage.setItem(COLLAPSE_KEY, JSON.stringify(true));

    renderQueue();
    expect(listPane().style.width).toBe("473px");

    // Expanding is the direction that *consumes* space, so the width that was
    // legal while collapsed must be clamped back down.
    await user.click(screen.getByRole("button", { name: "Show filters" }));

    expect(listPane().style.width).toBe("269px");
  });

  it("does not shrink the list when collapsing (collapsing only frees space)", async () => {
    const user = userEvent.setup();
    renderQueue();
    const before = listPane().style.width;
    expect(before).toBe("269px");

    await user.click(screen.getByRole("button", { name: "Hide filters" }));

    // The ceiling rises; clamping only lowers, so the list keeps its width and
    // the freed space goes to the flex-1 detail pane.
    expect(listPane().style.width).toBe(before);
  });
});

/**
 * The collapse feature end to end (N4e). The pieces above each test one
 * direction from a fresh fixture; these drive a single continuous session, so
 * an asymmetric or accumulating bug between the two directions has somewhere to
 * show up.
 */
describe("filter column — collapse round trip (N4e)", () => {
  const listPane = () => document.querySelector<HTMLElement>("aside")!;

  it("returns to its exact starting state after collapse → expand", async () => {
    const user = userEvent.setup();
    renderQueue();

    const before = {
      columnWidth: column().className.includes("w-64"),
      listWidth: listPane().style.width,
      collapsed: column().getAttribute("data-collapsed"),
    };

    await user.click(screen.getByRole("button", { name: "Hide filters" }));
    await user.click(screen.getByRole("button", { name: "Show filters" }));

    expect(column().className.includes("w-64")).toBe(before.columnWidth);
    expect(listPane().style.width).toBe(before.listWidth);
    expect(column().getAttribute("data-collapsed")).toBe(before.collapsed);
    expect(searchBox()).toBeInTheDocument();
  });

  it("stays stable across repeated cycles (no ratcheting)", async () => {
    const user = userEvent.setup();
    renderQueue();
    const startWidth = listPane().style.width;

    for (let i = 0; i < 3; i++) {
      await user.click(screen.getByRole("button", { name: "Hide filters" }));
      await user.click(screen.getByRole("button", { name: "Show filters" }));
    }

    // A width that crept in either direction each cycle would show up here.
    expect(listPane().style.width).toBe(startWidth);
    expect(column().className).toContain("w-64");
  });

  it("hands space to the detail pane while collapsed, and takes it back", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("confmail.queueListWidth", JSON.stringify(640));
    renderQueue();

    // Expanded: chrome is 315, so the list ceiling is 269.
    expect(listPane().style.width).toBe("269px");

    await user.click(screen.getByRole("button", { name: "Hide filters" }));
    // Collapsed frees 204px of chrome; the list keeps its width (clamping only
    // lowers) so the freed space goes to the flex-1 detail pane.
    expect(listPane().style.width).toBe("269px");
    expect(column().className).toContain("w-[52px]");

    await user.click(screen.getByRole("button", { name: "Show filters" }));
    expect(listPane().style.width).toBe("269px");
    expect(column().className).toContain("w-64");
  });
});

describe("filter column — toggle keyboard accessibility (N4e)", () => {
  it("is reachable by keyboard and operable with Enter in both states", async () => {
    const user = userEvent.setup();
    renderQueue();

    // Tab lands on the toggle: it's the first focusable control in the column.
    await user.tab();
    const toggle = screen.getByRole("button", { name: "Hide filters" });
    expect(toggle).toHaveFocus();

    await user.keyboard("{Enter}");
    expect(column()).toHaveAttribute("data-collapsed", "true");

    // Still focused and operable once collapsed — the only way back.
    const reopen = screen.getByRole("button", { name: "Show filters" });
    expect(reopen).toHaveFocus();

    await user.keyboard("{Enter}");
    expect(column()).toHaveAttribute("data-collapsed", "false");
  });

  it("is operable with Space", async () => {
    const user = userEvent.setup();
    renderQueue();

    await user.tab();
    await user.keyboard(" ");

    expect(column()).toHaveAttribute("data-collapsed", "true");
  });

  it("shows its tooltip on keyboard focus, not just hover", async () => {
    renderQueue();

    fireEvent.focus(screen.getByRole("button", { name: "Hide filters" }));

    await waitFor(() =>
      expect(screen.getByRole("tooltip")).toHaveTextContent("Hide filters")
    );
  });
});

describe("filter column — filter values survive a collapse cycle (N4e)", () => {
  it("retains search, lane, status and chair selections", async () => {
    const user = userEvent.setup();
    renderQueue();

    // Set every control to a non-default value…
    await user.type(searchBox(), "deadline");
    await user.click(screen.getByRole("button", { name: "Review" }));
    await user.selectOptions(screen.getAllByRole("combobox")[0], "APPROVED");
    await user.selectOptions(
      screen.getByLabelText("Filter by assigned chair"),
      "2"
    );

    // …collapse (which UNMOUNTS the panel) and expand again.
    await user.click(screen.getByRole("button", { name: "Hide filters" }));
    expect(
      screen.queryByPlaceholderText(/search subject or sender/i)
    ).toBeNull();
    await user.click(screen.getByRole("button", { name: "Show filters" }));

    // State lives in the page, not the unmounted panel, so nothing resets.
    expect(searchBox()).toHaveValue("deadline");
    expect(screen.getByRole("button", { name: "Review" }).style.color).toBe(
      "var(--accent)"
    );
    expect(screen.getAllByRole("combobox")[0]).toHaveValue("APPROVED");
    expect(screen.getByLabelText("Filter by assigned chair")).toHaveValue("2");
  });
});

describe("filter column — interactions still work page-owned", () => {
  it("types into the search box and keeps the value", async () => {
    const user = userEvent.setup();
    renderQueue();

    await user.type(searchBox(), "deadline");

    expect(searchBox()).toHaveValue("deadline");
  });

  it("switches the active lane pill on click", async () => {
    const user = userEvent.setup();
    renderQueue();

    const all = screen.getByRole("button", { name: "All" });
    const faq = screen.getByRole("button", { name: "FAQ" });
    expect(all.style.color).toBe("var(--accent)");

    await user.click(faq);

    expect(faq.style.color).toBe("var(--accent)");
    expect(all.style.color).toBe("var(--text-secondary)");
  });

  it("changes the status dropdown", async () => {
    const user = userEvent.setup();
    renderQueue();

    // Two selects in the panel: status first, then assigned chair.
    const status = screen.getAllByRole("combobox")[0];
    expect(status).toHaveValue("all");

    await user.selectOptions(status, "APPROVED");

    expect(status).toHaveValue("APPROVED");
  });

  it("changes the assigned-chair dropdown, listing the roster", async () => {
    const user = userEvent.setup();
    renderQueue();

    const chair = screen.getByLabelText("Filter by assigned chair");
    expect(chair).toHaveValue("all");
    expect(
      within(chair as HTMLSelectElement).getByRole("option", {
        name: "Program Chair",
      })
    ).toBeInTheDocument();

    await user.selectOptions(chair, "2");

    expect(chair).toHaveValue("2");
  });

  it("keeps filter state independent across controls", async () => {
    const user = userEvent.setup();
    renderQueue();

    await user.type(searchBox(), "grant");
    await user.click(screen.getByRole("button", { name: "Review" }));
    await user.selectOptions(screen.getAllByRole("combobox")[0], "PENDING");

    // Setting one filter must not reset the others.
    expect(searchBox()).toHaveValue("grant");
    expect(screen.getByRole("button", { name: "Review" }).style.color).toBe(
      "var(--accent)"
    );
    expect(screen.getAllByRole("combobox")[0]).toHaveValue("PENDING");
  });
});
