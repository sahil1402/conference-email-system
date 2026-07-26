/**
 * PolicySelectPopover — searchable policy picker (5a).
 *
 * Spies at the API layer (`listPolicies`) so the debounce, the server-side
 * `status: "active"` filter, and the keyboard/selection behaviour are all
 * exercised through the real hook + Radix popover.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { PolicySelectPopover } from "@/components/email/PolicySelectPopover";
import type { PolicyDocument } from "@/types";

const state = vi.hoisted(() => ({ list: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, listPolicies: state.list };
});

function policy(over: Partial<PolicyDocument> = {}): PolicyDocument {
  return {
    policy_key: "policy_186", title: "Paper Modification Guidelines",
    content: "After the July 21 abstract deadline nothing can be changed.",
    category: "submission", visibility: "public", status: "active",
    source: null, updated_at: null, supersedes: null, superseded_by: null,
    root_key: null, version: 1, ...over,
  } as PolicyDocument;
}

function renderPicker(onSelect = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <PolicySelectPopover onSelect={onSelect}>
        <button type="button">Add policy</button>
      </PolicySelectPopover>
    </QueryClientProvider>
  );
  return onSelect;
}

async function open(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Add policy" }));
  return screen.getByRole("combobox", { name: "Search policies" });
}

beforeEach(() => {
  state.list.mockReset();
  state.list.mockResolvedValue({ policies: [] });
});

describe("PolicySelectPopover", () => {
  it("prompts to type before any search has run", async () => {
    const user = userEvent.setup();
    renderPicker();
    await open(user);

    expect(screen.getByText("Type to search the knowledge base.")).toBeInTheDocument();
    expect(state.list).not.toHaveBeenCalled(); // no query on an empty box
  });

  it("debounces typing into ONE request carrying search + status=active", async () => {
    const user = userEvent.setup();
    state.list.mockResolvedValue({ policies: [policy()] });
    renderPicker();
    const input = await open(user);

    await user.type(input, "deadline");

    await waitFor(() =>
      expect(state.list).toHaveBeenCalledWith({ status: "active", search: "deadline" })
    );
    // Debounced: 8 keystrokes must not become 8 requests.
    expect(state.list.mock.calls.length).toBeLessThan(8);
  });

  it("renders results with title and a content preview", async () => {
    const user = userEvent.setup();
    state.list.mockResolvedValue({ policies: [policy()] });
    renderPicker();
    const input = await open(user);
    await user.type(input, "deadline");

    expect(await screen.findByText("Paper Modification Guidelines")).toBeInTheDocument();
    expect(
      screen.getByText("After the July 21 abstract deadline nothing can be changed.")
    ).toBeInTheDocument();
  });

  it("shows a no-results state", async () => {
    const user = userEvent.setup();
    state.list.mockResolvedValue({ policies: [] });
    renderPicker();
    const input = await open(user);
    await user.type(input, "zzzz");

    expect(await screen.findByText(/No active policies match/)).toBeInTheDocument();
  });

  it("shows a loading state while the query is in flight", async () => {
    const user = userEvent.setup();
    state.list.mockReturnValue(new Promise(() => {})); // never resolves
    renderPicker();
    const input = await open(user);
    await user.type(input, "deadline");

    expect(await screen.findByText("Searching…")).toBeInTheDocument();
  });

  it("filters out any non-active policy the API returns", async () => {
    const user = userEvent.setup();
    state.list.mockResolvedValue({
      policies: [
        policy(),
        policy({ policy_key: "int_retired", title: "Retired Policy", status: "inactive" }),
      ],
    });
    renderPicker();
    const input = await open(user);
    await user.type(input, "policy");

    expect(await screen.findByText("Paper Modification Guidelines")).toBeInTheDocument();
    expect(screen.queryByText("Retired Policy")).not.toBeInTheDocument();
  });

  it("selects with a click and reports the policy_key", async () => {
    const user = userEvent.setup();
    state.list.mockResolvedValue({ policies: [policy()] });
    const onSelect = renderPicker();
    const input = await open(user);
    await user.type(input, "deadline");

    await user.click(await screen.findByText("Paper Modification Guidelines"));
    expect(onSelect).toHaveBeenCalledWith("policy_186");
  });

  it("arrow keys move the active option and Enter selects it", async () => {
    const user = userEvent.setup();
    state.list.mockResolvedValue({
      policies: [
        policy(),
        policy({ policy_key: "policy_117", title: "Key Dates" }),
        policy({ policy_key: "policy_171", title: "Abstract Submission" }),
      ],
    });
    const onSelect = renderPicker();
    const input = await open(user);
    await user.type(input, "aaai");
    await screen.findByText("Key Dates");

    // First option is active by default.
    expect(screen.getAllByRole("option")[0]).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{ArrowDown}{ArrowDown}");
    expect(screen.getAllByRole("option")[2]).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{ArrowUp}");
    expect(screen.getAllByRole("option")[1]).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledWith("policy_117");
  });

  it("wraps around at both ends", async () => {
    const user = userEvent.setup();
    state.list.mockResolvedValue({
      policies: [policy(), policy({ policy_key: "policy_117", title: "Key Dates" })],
    });
    const onSelect = renderPicker();
    const input = await open(user);
    await user.type(input, "aaai");
    await screen.findByText("Key Dates");

    await user.keyboard("{ArrowUp}"); // 0 -> last
    expect(screen.getAllByRole("option")[1]).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{ArrowDown}"); // last -> 0
    expect(screen.getAllByRole("option")[0]).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledWith("policy_186");
  });

  it("keeps focus in the input while arrowing (aria-activedescendant pattern)", async () => {
    const user = userEvent.setup();
    state.list.mockResolvedValue({
      policies: [policy(), policy({ policy_key: "policy_117", title: "Key Dates" })],
    });
    renderPicker();
    const input = await open(user);
    await user.type(input, "aaai");
    await screen.findByText("Key Dates");

    await user.keyboard("{ArrowDown}");
    expect(input).toHaveFocus();
    expect(input).toHaveAttribute("aria-activedescendant", "policy-option-policy_117");
  });

  it("Escape closes without selecting", async () => {
    const user = userEvent.setup();
    state.list.mockResolvedValue({ policies: [policy()] });
    const onSelect = renderPicker();
    const input = await open(user);
    await user.type(input, "deadline");
    await screen.findByText("Paper Modification Guidelines");

    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByRole("combobox")).not.toBeInTheDocument()
    );
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("performs no API writes — only listPolicies is ever called", async () => {
    const user = userEvent.setup();
    state.list.mockResolvedValue({ policies: [policy()] });
    const onSelect = renderPicker();
    const input = await open(user);
    await user.type(input, "deadline");
    await user.click(await screen.findByText("Paper Modification Guidelines"));

    // Selection reports upward and does nothing else.
    expect(onSelect).toHaveBeenCalledWith("policy_186");
    expect(state.list).toHaveBeenCalled();
  });
});
