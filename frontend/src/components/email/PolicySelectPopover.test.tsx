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

const state = vi.hoisted(() => ({ list: vi.fn(), retry: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, listPolicies: state.list, retryEmail: state.retry };
});

const EMAIL_ID = 2662;

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
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={qc}>
      <PolicySelectPopover emailId={EMAIL_ID} onSelect={onSelect}>
        <button type="button">Add policy</button>
      </PolicySelectPopover>
    </QueryClientProvider>
  );
  return onSelect;
}

/** Search for a term and land on the detail view for the first result. */
async function openDetail(user: ReturnType<typeof userEvent.setup>, term = "deadline") {
  const input = await open(user);
  await user.type(input, term);
  await user.click(await screen.findByText("Paper Modification Guidelines"));
}

async function open(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Add policy" }));
  return screen.getByRole("combobox", { name: "Search policies" });
}

beforeEach(() => {
  state.list.mockReset();
  state.list.mockResolvedValue({ policies: [] });
  state.retry.mockReset();
  state.retry.mockResolvedValue({
    email_id: String(EMAIL_ID), redrafting: true, forced_policy_key: "policy_186",
  });
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

  it("clicking a result opens the DETAIL view, and redrafts nothing", async () => {
    const user = userEvent.setup();
    state.list.mockResolvedValue({ policies: [policy()] });
    const onSelect = renderPicker();
    await openDetail(user);

    // Detail affordances are present…
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Change" })).toBeInTheDocument();
    expect(screen.getByText("policy_186")).toBeInTheDocument();
    expect(screen.getByText("submission")).toBeInTheDocument();
    // …and the search box is gone (view switched, not appended).
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();

    // Selection alone must never write.
    expect(state.retry).not.toHaveBeenCalled();
    expect(onSelect).not.toHaveBeenCalled();
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
    // Enter opens the confirm step for the highlighted policy — no write yet.
    expect(await screen.findByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.getByText("Key Dates")).toBeInTheDocument();
    expect(state.retry).not.toHaveBeenCalled();
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("wraps around at both ends", async () => {
    const user = userEvent.setup();
    state.list.mockResolvedValue({
      policies: [policy(), policy({ policy_key: "policy_117", title: "Key Dates" })],
    });
    renderPicker();
    const input = await open(user);
    await user.type(input, "aaai");
    await screen.findByText("Key Dates");

    await user.keyboard("{ArrowUp}"); // 0 -> last
    expect(screen.getAllByRole("option")[1]).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{ArrowDown}"); // last -> 0
    expect(screen.getAllByRole("option")[0]).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{Enter}");
    expect(await screen.findByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(state.retry).not.toHaveBeenCalled();
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

  // --- Detail / confirm view + Approve wiring (5b/5c) ------------------------

  it("detail view shows the FULL content, not the clamped preview", async () => {
    const user = userEvent.setup();
    const long =
      "PARA ONE about deadlines.\n\nPARA TWO with the fine print that a clamped preview would hide.";
    state.list.mockResolvedValue({ policies: [policy({ content: long })] });
    renderPicker();
    await openDetail(user);

    const el = screen.getByText(/PARA ONE about deadlines/);
    expect(el).toHaveTextContent("PARA TWO with the fine print");
    expect(el.className).not.toContain("line-clamp");
    // Newlines preserved, like PolicyDetailModal's body.
    expect(el).toHaveStyle({ whiteSpace: "pre-wrap" });
  });

  it("Approve posts the redraft with the email id AND forced_policy_key", async () => {
    const user = userEvent.setup();
    state.list.mockResolvedValue({ policies: [policy()] });
    const onSelect = renderPicker();
    await openDetail(user);

    await user.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() => expect(state.retry).toHaveBeenCalledWith(EMAIL_ID, "policy_186"));
    expect(state.retry).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith("policy_186"));
  });

  it("closes the popover after a successful Approve", async () => {
    const user = userEvent.setup();
    state.list.mockResolvedValue({ policies: [policy()] });
    renderPicker();
    await openDetail(user);

    await user.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument()
    );
  });

  it("shows a Re-drafting… state while the request is in flight", async () => {
    const user = userEvent.setup();
    state.list.mockResolvedValue({ policies: [policy()] });
    state.retry.mockReturnValue(new Promise(() => {})); // never resolves
    renderPicker();
    await openDetail(user);

    await user.click(screen.getByRole("button", { name: "Approve" }));

    expect(await screen.findByText("Re-drafting…")).toBeInTheDocument();
    // Both actions are locked so a double-click can't fire two redrafts.
    expect(screen.getByRole("button", { name: "Change" })).toBeDisabled();
  });

  it("keeps the popover open and surfaces an error if the redraft fails", async () => {
    const user = userEvent.setup();
    state.list.mockResolvedValue({ policies: [policy()] });
    state.retry.mockRejectedValue({ detail: "Email 2662 not found" });
    renderPicker();
    await openDetail(user);

    await user.click(screen.getByRole("button", { name: "Approve" }));

    expect(await screen.findByText("Email 2662 not found")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
  });

  it("Change returns to search with the prior query and results intact, no call", async () => {
    const user = userEvent.setup();
    state.list.mockResolvedValue({ policies: [policy()] });
    renderPicker();
    await openDetail(user);
    const callsAfterSearch = state.list.mock.calls.length;

    await user.click(screen.getByRole("button", { name: "Change" }));

    const input = screen.getByRole("combobox", { name: "Search policies" });
    expect(input).toHaveValue("deadline");                       // query preserved
    expect(screen.getByText("Paper Modification Guidelines")).toBeInTheDocument(); // results intact
    expect(state.list.mock.calls.length).toBe(callsAfterSearch); // served from cache
    expect(state.retry).not.toHaveBeenCalled();
  });

  it("the back arrow behaves like Change", async () => {
    const user = userEvent.setup();
    state.list.mockResolvedValue({ policies: [policy()] });
    renderPicker();
    await openDetail(user);

    await user.click(screen.getByRole("button", { name: "Back to search" }));

    expect(screen.getByRole("combobox", { name: "Search policies" })).toHaveValue("deadline");
    expect(state.retry).not.toHaveBeenCalled();
  });

  it("reopening after a close starts fresh on the search view", async () => {
    const user = userEvent.setup();
    state.list.mockResolvedValue({ policies: [policy()] });
    renderPicker();
    await openDetail(user);
    await user.keyboard("{Escape}");

    const input = await open(user);
    expect(input).toHaveValue("");
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });
  // --- Modal shell (Radix Dialog) --------------------------------------------

  it("opens as a modal dialog, centred and named", async () => {
    const user = userEvent.setup();
    renderPicker();
    await open(user);

    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeInTheDocument();
    expect(dialog).toHaveAttribute("data-state", "open");
    // Accessible name comes from the visually-hidden DialogTitle. (Radix does
    // NOT set an `aria-modal` attribute — it hides outside content with
    // aria-hidden instead — so asserting that attribute would be wrong.)
    expect(dialog).toHaveAccessibleName("Add a policy to this draft");
    // Centred in the viewport, and meaningfully wider than the old ~320px popover.
    expect(dialog.className).toContain("left-1/2");
    expect(dialog.className).toContain("top-1/2");
    expect(dialog.className).toContain("-translate-x-1/2");
    expect(dialog.className).toContain("-translate-y-1/2");
    expect(dialog.className).toContain("max-w-lg"); // 512px
    expect(dialog.className).not.toContain("w-80"); // the popover width is gone
  });

  it("renders a TRANSLUCENT backdrop over the page", async () => {
    const user = userEvent.setup();
    renderPicker();
    await open(user);

    const overlay = screen.getByTestId("dialog-overlay");
    expect(overlay).toBeInTheDocument();
    // Dimmed, not opaque — the ticket must stay visible behind it.
    const bg = overlay.style.backgroundColor;
    expect(bg).toMatch(/rgba\(0,\s*0,\s*0,\s*0\.6\)/);
    expect(bg).not.toBe("rgb(0, 0, 0)");
  });

  it("clicking the backdrop closes it without any API call", async () => {
    const user = userEvent.setup();
    state.list.mockResolvedValue({ policies: [policy()] });
    const onSelect = renderPicker();
    const input = await open(user);
    await user.type(input, "deadline");
    await screen.findByText("Paper Modification Guidelines");

    await user.click(screen.getByTestId("dialog-overlay"));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(state.retry).not.toHaveBeenCalled();
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("Escape closes the dialog and triggers no API call", async () => {
    const user = userEvent.setup();
    state.list.mockResolvedValue({ policies: [policy()] });
    const onSelect = renderPicker();
    const input = await open(user);
    await user.type(input, "deadline");
    await screen.findByText("Paper Modification Guidelines");

    await user.keyboard("{Escape}");

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(state.retry).not.toHaveBeenCalled();
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("Escape from the DETAIL view also closes without writing", async () => {
    const user = userEvent.setup();
    state.list.mockResolvedValue({ policies: [policy()] });
    renderPicker();
    await openDetail(user);
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();

    await user.keyboard("{Escape}");

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(state.retry).not.toHaveBeenCalled();
  });

  it("keeps the dialog role while on the detail view", async () => {
    const user = userEvent.setup();
    state.list.mockResolvedValue({ policies: [policy()] });
    renderPicker();
    await openDetail(user);

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toHaveAccessibleName("Confirm policy");
  });
});
