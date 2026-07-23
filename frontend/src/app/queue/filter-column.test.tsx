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
import { render, screen, within } from "@testing-library/react";
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

beforeEach(() => {
  window.localStorage.clear();
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
  const column = () => searchBox().closest<HTMLElement>("div.w-64")!;

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
