import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { PolicyEditor } from "./PolicyEditor";

const state = vi.hoisted(() => ({ editPolicy: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  editPolicy: state.editPolicy,
}));

function renderEditor(props: Partial<React.ComponentProps<typeof PolicyEditor>> = {}) {
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <PolicyEditor
        policyKey="policy_b"
        initialTitle="Reviewer deadline"
        initialContent="Reviews are due within 14 days."
        onDone={vi.fn()}
        onCancel={vi.fn()}
        {...props}
      />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  state.editPolicy.mockReset();
  state.editPolicy.mockResolvedValue({});
});

describe("PolicyEditor visibility preservation", () => {
  it("omits visibility (backend preserves it) when the current one is unknown", async () => {
    const user = userEvent.setup();
    renderEditor({ initialVisibility: undefined });

    // No visibility control is shown → nothing can flip public↔internal.
    expect(screen.queryByRole("combobox")).toBeNull();

    await user.click(screen.getByRole("button", { name: /commit edit/i }));
    await waitFor(() => expect(state.editPolicy).toHaveBeenCalled());
    const [key, body] = state.editPolicy.mock.calls[0];
    expect(key).toBe("policy_b");
    expect(body.visibility).toBeUndefined();
  });

  it("sends the chosen visibility when the current one is known", async () => {
    const user = userEvent.setup();
    renderEditor({ initialVisibility: "public" });

    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(select.value).toBe("public"); // seeded from the real current value
    await user.click(screen.getByRole("button", { name: /commit edit/i }));
    await waitFor(() => expect(state.editPolicy).toHaveBeenCalled());
    const [, body] = state.editPolicy.mock.calls[0];
    expect(body.visibility).toBe("public");
  });
});
